"""Mount-free ext4 reader for forensic, offline analysis of a regular disk image.

The image is opened in binary read-only mode. This module never mounts images,
executes image contents, or writes to the source image.
"""
from __future__ import annotations

import math
import os
import stat
import struct
import uuid as uuidlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


class Ext4Error(RuntimeError):
    """Raised when the image, ext4 metadata, or requested path is invalid."""


@dataclass(frozen=True)
class Partition:
    index: int
    start_lba: int
    sectors: int
    offset: int
    size: int


@dataclass(frozen=True)
class Inode:
    number: int
    mode: int
    size: int
    flags: int
    blocks_512: int
    raw: bytes


@dataclass(frozen=True)
class DirEntry:
    name: str
    inode: int
    file_type: int


class Ext4Image:
    """Read MBR-partitioned ext4 files by UUID using pread-style file seeks."""

    EXT4_MAGIC = 0xEF53
    FEATURE_INCOMPAT_64BIT = 0x0080
    INODE_EXTENTS = 0x00080000
    EXTENT_MAGIC = 0xF30A
    MAX_FILE_BYTES = 512 * 1024 * 1024

    def __init__(self, image_path: str | os.PathLike[str], expected_uuid: str | None = None):
        self.image_path = Path(image_path)
        self._fp: BinaryIO = open(self.image_path, "rb")
        self.image_size = os.fstat(self._fp.fileno()).st_size
        self.read_only = True
        self._inode_cache: dict[int, Inode] = {}
        try:
            self.partitions = self._read_mbr()
            self.partition, sb = self._find_ext4_partition(expected_uuid)
            self.partition_offset = self.partition.offset
            self._load_superblock(sb)
        except Exception:
            self._fp.close()
            raise

    def __enter__(self) -> "Ext4Image":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.close()

    def _read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.image_size:
            raise Ext4Error(f"image read outside bounds: offset={offset}, length={length}")
        self._fp.seek(offset)
        data = self._fp.read(length)
        if len(data) != length:
            raise Ext4Error(f"short image read at offset {offset}: wanted {length}, got {len(data)}")
        return data

    def _read_mbr(self) -> list[Partition]:
        if self.image_size < 512:
            raise Ext4Error("image is shorter than an MBR sector")
        mbr = self._read_at(0, 512)
        if mbr[510:512] != b"\x55\xaa":
            raise Ext4Error("MBR signature missing; GPT/raw images are not accepted by this reader")
        found: list[Partition] = []
        for slot in range(4):
            off = 446 + slot * 16
            kind = mbr[off + 4]
            start_lba, sectors = struct.unpack_from("<II", mbr, off + 8)
            if kind == 0 or sectors == 0:
                continue
            part = Partition(
                index=slot + 1,
                start_lba=start_lba,
                sectors=sectors,
                offset=start_lba * 512,
                size=sectors * 512,
            )
            if part.offset + part.size > self.image_size:
                raise Ext4Error(f"partition {slot + 1} extends beyond image size")
            found.append(part)
        if not found:
            raise Ext4Error("no MBR partitions found")
        return found

    def _find_ext4_partition(self, expected_uuid: str | None) -> tuple[Partition, bytes]:
        matches: list[tuple[Partition, bytes, str]] = []
        for part in self.partitions:
            if part.size < 2048:
                continue
            sb = self._read_at(part.offset + 1024, 1024)
            if struct.unpack_from("<H", sb, 56)[0] != self.EXT4_MAGIC:
                continue
            found_uuid = str(uuidlib.UUID(bytes=sb[104:120]))
            if expected_uuid is None or found_uuid.lower() == str(expected_uuid).lower():
                matches.append((part, sb, found_uuid))
        if not matches:
            if expected_uuid:
                raise Ext4Error(f"no ext4 MBR partition with UUID {expected_uuid}")
            raise Ext4Error("no ext4 superblock found in MBR partitions")
        if len(matches) != 1:
            raise Ext4Error("more than one ext4 partition matched; refusing ambiguous selection")
        part, sb, _ = matches[0]
        return part, sb

    def _load_superblock(self, sb: bytes) -> None:
        self.uuid = str(uuidlib.UUID(bytes=sb[104:120]))
        self.inodes_count = struct.unpack_from("<I", sb, 0)[0]
        blocks_lo = struct.unpack_from("<I", sb, 4)[0]
        self.feature_incompat = struct.unpack_from("<I", sb, 96)[0]
        self.state = struct.unpack_from("<H", sb, 58)[0]
        self.needs_recovery = bool(self.feature_incompat & 0x00000004)
        supported_incompat = 0x00000002 | 0x00000040 | self.FEATURE_INCOMPAT_64BIT | 0x00000200
        unknown_incompat = self.feature_incompat & ~supported_incompat
        if unknown_incompat:
            raise Ext4Error(f"unsupported ext4 incompat feature bits 0x{unknown_incompat:x}")
        blocks_hi = struct.unpack_from("<I", sb, 336)[0] if self.feature_incompat & self.FEATURE_INCOMPAT_64BIT else 0
        self.blocks_count = blocks_lo | (blocks_hi << 32)
        self.first_data_block = struct.unpack_from("<I", sb, 20)[0]
        log_block_size = struct.unpack_from("<I", sb, 24)[0]
        if log_block_size > 6:
            raise Ext4Error(f"unsupported ext4 block size exponent {log_block_size}")
        self.block_size = 1024 << log_block_size
        self.blocks_per_group = struct.unpack_from("<I", sb, 32)[0]
        self.inodes_per_group = struct.unpack_from("<I", sb, 40)[0]
        revision = struct.unpack_from("<I", sb, 76)[0]
        self.inode_size = struct.unpack_from("<H", sb, 88)[0] if revision else 128
        self.desc_size = struct.unpack_from("<H", sb, 254)[0] if self.feature_incompat & self.FEATURE_INCOMPAT_64BIT else 32
        if self.desc_size < 32 or self.desc_size > self.block_size:
            raise Ext4Error(f"invalid group descriptor size {self.desc_size}")
        if self.inode_size < 128 or self.inode_size > self.block_size or self.inode_size % 4:
            raise Ext4Error(f"invalid inode size {self.inode_size}")
        if not self.blocks_per_group or not self.inodes_per_group:
            raise Ext4Error("zero ext4 group geometry")
        fs_bytes = self.blocks_count * self.block_size
        if fs_bytes > self.partition.size:
            raise Ext4Error("ext4 block count exceeds the containing partition")
        self.group_count = math.ceil(max(0, self.blocks_count - self.first_data_block) / self.blocks_per_group)
        if self.group_count < 1:
            raise Ext4Error("ext4 filesystem has no block groups")
        self.gdt_block = self.first_data_block + 1

    def _part_read(self, relative_offset: int, length: int) -> bytes:
        if relative_offset < 0 or length < 0 or relative_offset + length > self.partition.size:
            raise Ext4Error(f"filesystem read outside partition: offset={relative_offset}, length={length}")
        return self._read_at(self.partition.offset + relative_offset, length)

    def _read_blocks(self, first_block: int, block_count: int) -> bytes:
        if first_block < 0 or block_count < 0 or first_block + block_count > self.blocks_count:
            raise Ext4Error(f"filesystem block range outside ext4: start={first_block}, count={block_count}")
        return self._part_read(first_block * self.block_size, block_count * self.block_size)

    def _group_descriptor(self, group: int) -> bytes:
        if group < 0 or group >= self.group_count:
            raise Ext4Error(f"invalid block group {group}")
        byte_offset = self.gdt_block * self.block_size + group * self.desc_size
        return self._part_read(byte_offset, self.desc_size)

    def inode(self, inode_no: int) -> Inode:
        cached = self._inode_cache.get(inode_no)
        if cached is not None:
            return cached
        if inode_no < 1 or inode_no > self.inodes_count:
            raise Ext4Error(f"invalid inode number {inode_no}")
        group = (inode_no - 1) // self.inodes_per_group
        local_index = (inode_no - 1) % self.inodes_per_group
        gd = self._group_descriptor(group)
        inode_table = struct.unpack_from("<I", gd, 8)[0]
        if self.feature_incompat & self.FEATURE_INCOMPAT_64BIT:
            if self.desc_size < 64:
                raise Ext4Error("64-bit ext4 filesystem has a short group descriptor")
            inode_table |= struct.unpack_from("<I", gd, 40)[0] << 32
        inode_offset = inode_table * self.block_size + local_index * self.inode_size
        raw = self._part_read(inode_offset, self.inode_size)
        mode = struct.unpack_from("<H", raw, 0)[0]
        size = struct.unpack_from("<I", raw, 4)[0] | (struct.unpack_from("<I", raw, 108)[0] << 32)
        flags = struct.unpack_from("<I", raw, 32)[0]
        blocks_512 = struct.unpack_from("<I", raw, 28)[0]
        obj = Inode(inode_no, mode, size, flags, blocks_512, raw)
        self._inode_cache[inode_no] = obj
        return obj

    def _extent_records(self, node: bytes, depth_expected: int | None = None) -> list[tuple[int, int, int, bool]]:
        if len(node) < 12 or struct.unpack_from("<H", node, 0)[0] != self.EXTENT_MAGIC:
            raise Ext4Error("invalid extent tree header")
        entries, maximum, depth = struct.unpack_from("<HHH", node, 2)
        if entries > maximum or 12 + entries * 12 > len(node):
            raise Ext4Error("invalid extent tree entry count")
        if depth_expected is not None and depth != depth_expected:
            raise Ext4Error("inconsistent extent tree depth")
        if depth > 5:
            raise Ext4Error("extent tree depth exceeds the supported safety bound of 5")
        result: list[tuple[int, int, int, bool]] = []
        for index in range(entries):
            off = 12 + index * 12
            if depth == 0:
                logical, length_field, start_hi, start_lo = struct.unpack_from("<IHHI", node, off)
                # ext4 stores initialized lengths through 0x8000 inclusive;
                # larger values encode unwritten length as value - 0x8000.
                uninitialized = length_field > 0x8000
                length = length_field - 0x8000 if uninitialized else length_field
                if length == 0:
                    raise Ext4Error("zero-length extent")
                physical = (start_hi << 32) | start_lo
                if physical + length > self.blocks_count:
                    raise Ext4Error("extent points beyond ext4 filesystem")
                result.append((logical, physical, length, uninitialized))
            else:
                logical, leaf_lo, leaf_hi = struct.unpack_from("<IIH", node, off)
                child_block = (leaf_hi << 32) | leaf_lo
                child = self._read_blocks(child_block, 1)
                result.extend(self._extent_records(child, depth - 1))
        return result

    def _inode_extents(self, inode: Inode) -> list[tuple[int, int, int, bool]]:
        if not inode.flags & self.INODE_EXTENTS:
            raise Ext4Error(f"inode {inode.number} does not use extents")
        return self._extent_records(inode.raw[40:100])

    def read_inode(self, inode: Inode) -> bytes:
        if inode.size > self.MAX_FILE_BYTES:
            raise Ext4Error(f"refusing large file read ({inode.size} bytes)")
        if inode.size == 0:
            return b""
        if stat.S_ISLNK(inode.mode) and inode.size <= 60 and inode.blocks_512 == 0:
            return inode.raw[40 : 40 + inode.size]
        if inode.flags & 0x10000000:  # EXT4_INLINE_DATA_FL
            raise Ext4Error(f"inline-data inode {inode.number} is unsupported")
        if not inode.flags & self.INODE_EXTENTS:
            raise Ext4Error(f"non-extent inode {inode.number} is unsupported")
        extents = sorted(self._inode_extents(inode), key=lambda rec: rec[0])
        out = bytearray()
        logical_block = 0
        wanted_blocks = math.ceil(inode.size / self.block_size)
        for logical, physical, count, uninitialized in extents:
            if logical >= wanted_blocks:
                break
            if logical > logical_block:
                out.extend(bytes((logical - logical_block) * self.block_size))
                logical_block = logical
            if logical < logical_block:
                raise Ext4Error(f"overlapping extents in inode {inode.number}")
            take = min(count, wanted_blocks - logical)
            if uninitialized:
                out.extend(bytes(take * self.block_size))
            else:
                out.extend(self._read_blocks(physical, take))
            logical_block += take
        if logical_block < wanted_blocks:
            out.extend(bytes((wanted_blocks - logical_block) * self.block_size))
        return bytes(out[: inode.size])

    def _directory_entries(self, inode: Inode) -> list[DirEntry]:
        if not stat.S_ISDIR(inode.mode):
            raise Ext4Error(f"inode {inode.number} is not a directory")
        raw = self.read_inode(inode)
        entries: list[DirEntry] = []
        offset = 0
        while offset + 8 <= len(raw):
            number, rec_len, name_len, file_type = struct.unpack_from("<IHBB", raw, offset)
            if rec_len < 8 or rec_len % 4 or offset + rec_len > len(raw):
                raise Ext4Error(f"invalid directory record in inode {inode.number} at {offset}")
            if name_len > rec_len - 8:
                raise Ext4Error(f"invalid directory name length in inode {inode.number}")
            if number:
                name_bytes = raw[offset + 8 : offset + 8 + name_len]
                name = name_bytes.decode("utf-8", errors="surrogateescape")
                entries.append(DirEntry(name, number, file_type))
            offset += rec_len
        if offset != len(raw) and any(raw[offset:]):
            raise Ext4Error(f"nonzero trailing bytes in directory inode {inode.number}")
        return entries

    def listdir_inode(self, inode: Inode) -> list[DirEntry]:
        return self._directory_entries(inode)

    def listdir(self, path: str) -> list[DirEntry]:
        return self._directory_entries(self.resolve(path))

    def resolve(self, path: str, *, follow_symlinks: bool = True) -> Inode:
        if not path.startswith("/"):
            path = "/" + path
        pending = deque(part for part in path.split("/") if part not in ("", "."))
        current = self.inode(2)
        parents: list[Inode] = [current]
        symlink_hops = 0
        while pending:
            component = pending.popleft()
            if component == "..":
                if len(parents) > 1:
                    parents.pop()
                current = parents[-1]
                continue
            if not stat.S_ISDIR(current.mode):
                raise Ext4Error(f"path component parent is not a directory: {component}")
            match = next((entry for entry in self._directory_entries(current) if entry.name == component), None)
            if match is None:
                raise FileNotFoundError(path)
            child = self.inode(match.inode)
            if stat.S_ISLNK(child.mode) and follow_symlinks:
                symlink_hops += 1
                if symlink_hops > 40:
                    raise Ext4Error("too many symbolic-link levels")
                target = self.read_inode(child).decode("utf-8", errors="surrogateescape")
                target_parts = [part for part in target.split("/") if part not in ("", ".")]
                remainder = list(pending)
                pending.clear()
                pending.extend(target_parts + remainder)
                if target.startswith("/"):
                    current = self.inode(2)
                    parents = [current]
                # A relative target is interpreted from the symlink's containing directory.
                continue
            current = child
            if stat.S_ISDIR(current.mode):
                parents.append(current)
            elif pending:
                raise Ext4Error(f"non-directory path component has a suffix: {component}")
        return current

    def read_file(self, path: str) -> bytes:
        inode = self.resolve(path)
        if not stat.S_ISREG(inode.mode) and not stat.S_ISLNK(inode.mode):
            raise Ext4Error(f"path is not a regular file: {path}")
        return self.read_inode(inode)

    def walk(self, start: str = "/", *, max_directories: int = 250_000) -> Iterator[tuple[str, Inode]]:
        """Yield directory descendants without following symlinks."""
        root = self.resolve(start)
        if not stat.S_ISDIR(root.mode):
            raise Ext4Error(f"walk start is not a directory: {start}")
        queue = deque([(start.rstrip("/") or "/", root)])
        seen = {root.number}
        count = 0
        while queue:
            parent_path, directory = queue.popleft()
            count += 1
            if count > max_directories:
                raise Ext4Error(f"directory walk limit exceeded ({max_directories})")
            for entry in self._directory_entries(directory):
                if entry.name in (".", ".."):
                    continue
                child = self.inode(entry.inode)
                path = parent_path.rstrip("/") + "/" + entry.name
                yield path, child
                if stat.S_ISDIR(child.mode) and child.number not in seen:
                    seen.add(child.number)
                    queue.append((path, child))