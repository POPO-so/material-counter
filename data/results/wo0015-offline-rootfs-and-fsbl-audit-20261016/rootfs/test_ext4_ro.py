"""Focused tests for a mount-free, read-only ext4 image reader."""
from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from ext4_ro import Ext4Error, Ext4Image


PART_LBA = 8
BLOCK_SIZE = 1024
BLOCK_COUNT = 64
UUID = bytes.fromhex("63b8b04daf08419aa9dc16f374e283dc")


def _put_inode(buf: bytearray, inode_no: int, mode: int, size: int, data_block: int) -> None:
    table_offset = PART_LBA * 512 + 3 * BLOCK_SIZE
    inode_offset = table_offset + (inode_no - 1) * 128
    struct.pack_into("<H", buf, inode_offset, mode)
    struct.pack_into("<I", buf, inode_offset + 4, size)
    struct.pack_into("<H", buf, inode_offset + 26, 2)
    struct.pack_into("<I", buf, inode_offset + 32, 0x00080000)  # EXT4_EXTENTS_FL
    struct.pack_into("<HHHHI", buf, inode_offset + 40, 0xF30A, 1, 4, 0, 0)
    struct.pack_into("<IHHI", buf, inode_offset + 52, 0, 1, 0, data_block)


def _dirent(inode_no: int, rec_len: int, name: bytes, file_type: int) -> bytes:
    return struct.pack("<IHBB", inode_no, rec_len, len(name), file_type) + name + bytes(rec_len - 8 - len(name))


def make_image(path: Path, *, file_extent: int = 6) -> None:
    part_offset = PART_LBA * 512
    image = bytearray(part_offset + BLOCK_COUNT * BLOCK_SIZE)
    # MBR partition 1: Linux type 0x83, eight-sector prefix, one ext4 partition.
    image[446 + 4] = 0x83
    struct.pack_into("<II", image, 446 + 8, PART_LBA, BLOCK_COUNT * 2)
    image[510:512] = b"\x55\xaa"

    sb = part_offset + BLOCK_SIZE
    struct.pack_into("<I", image, sb + 0, 16)  # inodes
    struct.pack_into("<I", image, sb + 4, BLOCK_COUNT)
    struct.pack_into("<I", image, sb + 20, 1)  # first data block
    struct.pack_into("<I", image, sb + 24, 0)  # 1 KiB blocks
    struct.pack_into("<I", image, sb + 32, BLOCK_COUNT)
    struct.pack_into("<I", image, sb + 40, 16)
    struct.pack_into("<H", image, sb + 56, 0xEF53)
    struct.pack_into("<I", image, sb + 76, 1)  # dynamic revision
    struct.pack_into("<H", image, sb + 88, 128)
    struct.pack_into("<I", image, sb + 96, 0x40)  # extents incompat feature
    image[sb + 104 : sb + 120] = UUID

    # Group descriptor table is block 2; its inode table begins at block 3.
    struct.pack_into("<I", image, part_offset + 2 * BLOCK_SIZE + 8, 3)
    _put_inode(image, 2, 0x41ED, BLOCK_SIZE, 5)  # root directory
    _put_inode(image, 3, 0x81A4, 12, file_extent)

    directory = (
        _dirent(2, 12, b".", 2)
        + _dirent(2, 12, b"..", 2)
        + _dirent(3, BLOCK_SIZE - 24, b"hello.txt", 1)
    )
    image[part_offset + 5 * BLOCK_SIZE : part_offset + 6 * BLOCK_SIZE] = directory
    if 0 <= file_extent < BLOCK_COUNT:
        image[part_offset + file_extent * BLOCK_SIZE : part_offset + file_extent * BLOCK_SIZE + 12] = b"hello, ext4\n"
    path.write_bytes(image)


class Ext4ReadOnlyTests(unittest.TestCase):
    def test_finds_requested_uuid_without_mounting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "disk.img"
            make_image(image)
            with Ext4Image(image, expected_uuid="63b8b04d-af08-419a-a9dc-16f374e283dc") as fs:
                self.assertEqual(fs.partition_offset, PART_LBA * 512)
                self.assertEqual(fs.uuid, "63b8b04d-af08-419a-a9dc-16f374e283dc")

    def test_reads_file_by_inode_extent_and_directory_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "disk.img"
            make_image(image)
            with Ext4Image(image, expected_uuid="63b8b04d-af08-419a-a9dc-16f374e283dc") as fs:
                self.assertEqual(fs.read_file("/hello.txt"), b"hello, ext4\n")

    def test_ee_len_0x8000_is_initialized_32768_blocks(self) -> None:
        fs = object.__new__(Ext4Image)
        fs.blocks_count = 32774
        node = bytearray(60)
        struct.pack_into("<HHHHI", node, 0, 0xF30A, 1, 4, 0, 0)
        struct.pack_into("<IHHI", node, 12, 0, 0x8000, 0, 6)
        self.assertEqual(fs._extent_records(bytes(node)), [(0, 6, 32768, False)])
    def test_rejects_extent_outside_partition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "disk.img"
            make_image(image, file_extent=BLOCK_COUNT + 3)
            with Ext4Image(image, expected_uuid="63b8b04d-af08-419a-a9dc-16f374e283dc") as fs:
                with self.assertRaises(Ext4Error):
                    fs.read_file("/hello.txt")


if __name__ == "__main__":
    unittest.main()
