import importlib.util
import struct
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("boot_image_reader", Path(__file__).with_name("boot_image_reader.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def boot_fixture():
    b = bytearray(0xA10)
    struct.pack_into("<10I", b, 0x20, 0xAA995566, 0x584C4E58, 0, 0x01010000, 0xA00, 16, 0, 0, 16, 1)
    struct.pack_into("<I", b, 0x48, ~sum(struct.unpack_from("<10I", b, 0x20)) & 0xFFFFFFFF)
    struct.pack_into("<II", b, 0x98, 0x900, 0x980)
    for i in range(256): struct.pack_into("<II", b, 0xA0 + i * 8, 0xFFFFFFFF, 0)
    struct.pack_into("<4I", b, 0x900, 0x01010000, 1, 0x980 // 4, 0x940 // 4)
    struct.pack_into("<4I", b, 0x940, 0, 0x980 // 4, 0, 1)
    b[0x950:0x958] = b"lbsffle."
    words = [4, 4, 4, 0, 0, 0xA00 // 4, 0, 1, 0, 0x940 // 4, 0, 0, 0, 0, 0]
    struct.pack_into("<16I", b, 0x980, *words, ~sum(words) & 0xFFFFFFFF)
    struct.pack_into("<I", b, 0x9FC, 0xFFFFFFFF)
    b[0xA00:0xA10] = b"STATIC_FSBL_DATA!"
    return b


def test_boot_headers_checksum_and_names():
    d = m.parse_zynq_boot(boot_fixture())
    assert d["fsbl_source_offset"] == 0xA00
    assert d["partitions"][0]["image_name"] == "fsbl.elf"
    assert d["partitions"][0]["data_nbytes"] == 16
    assert d["bootrom_register_initialization_pairs"] == []
    assert d["execution_evidence"].startswith("NONE")


@pytest.mark.parametrize("offset", [0x20, 0x24, 0x48, 0x980, 0x9BC])
def test_boot_corrupt_header_rejected(offset):
    b = boot_fixture(); b[offset] ^= 1
    with pytest.raises(ValueError): m.parse_zynq_boot(b)


def test_boot_partition_out_of_bounds_even_with_valid_checksum():
    b = boot_fixture()
    words = list(struct.unpack_from("<16I", b, 0x980)); words[5] = 0x100000
    words[-1] = ~sum(words[:-1]) & 0xFFFFFFFF
    struct.pack_into("<16I", b, 0x980, *words)
    with pytest.raises(ValueError): m.parse_zynq_boot(b)


def fat_fixture():
    b = bytearray(5008 * 512)
    b[510:512] = b"\x55\xaa"
    b[450] = 0x0C
    struct.pack_into("<II", b, 454, 8, 5000)
    base = 8 * 512
    struct.pack_into("<H", b, base + 11, 512)
    b[base + 13] = 1
    struct.pack_into("<H", b, base + 14, 1)
    b[base + 16] = 1
    struct.pack_into("<H", b, base + 17, 16)
    struct.pack_into("<H", b, base + 19, 5000)
    struct.pack_into("<H", b, base + 22, 20)
    b[base + 510:base + 512] = b"\x55\xaa"
    struct.pack_into("<H", b, base + 512 + 4, 0xFFF8)
    root = base + 21 * 512
    b[root:root + 11] = b"BOOT    BIN"
    b[root + 11] = 0x20
    struct.pack_into("<H", b, root + 26, 2)
    struct.pack_into("<I", b, root + 28, 6)
    b[base + 22 * 512:base + 22 * 512 + 6] = b"ABCDEF"
    return b


def test_fat16_from_bpb_not_mbr_type_label():
    b = fat_fixture()
    p = m.parse_mbr(b[:512], len(b))[0]
    fs = m.Fat16(lambda o, n: b[o:o + n], p)
    raw, evidence = fs.extract("BOOT.BIN")
    assert fs.bpb["filesystem_from_bpb"] == "FAT16"
    assert fs.bpb["partition_type_hex"] == "0x0C"
    assert raw == b"ABCDEF" and evidence["clusters_read"] == 1


def test_fat_invalid_cycle_or_extra_allocation_rejected():
    b = fat_fixture(); struct.pack_into("<H", b, 8 * 512 + 512 + 4, 2)
    p = m.parse_mbr(b[:512], len(b))[0]
    fs = m.Fat16(lambda o, n: b[o:o + n], p)
    with pytest.raises(ValueError): fs.extract("BOOT.BIN")


def test_mbr_bounds_and_signature():
    b = fat_fixture()
    with pytest.raises(ValueError): m.parse_mbr(b[:512], 512)
    b[510] = 0
    with pytest.raises(ValueError): m.parse_mbr(b[:512], len(b))


def test_extract_limit_and_missing_file():
    b = fat_fixture(); p = m.parse_mbr(b[:512], len(b))[0]
    fs = m.Fat16(lambda o, n: b[o:o + n], p)
    with pytest.raises(ValueError): fs.extract("BOOT.BIN", limit=2)
    with pytest.raises(ValueError): fs.extract("FSBL.ELF")
