"""Bounded data-only MBR/FAT16/Zynq readers. Never mount or execute an image."""
import hashlib
import struct


def checked_span(offset, size, total):
    if min(offset, size) < 0 or offset + size > total:
        raise ValueError("Out-of-bounds image span")


def parse_mbr(raw, total):
    if len(raw) != 512 or raw[510:512] != b"\x55\xaa": raise ValueError("Invalid MBR signature")
    partitions = []
    for i in range(4):
        entry = raw[446 + i * 16:462 + i * 16]
        kind = entry[4]
        start, count = struct.unpack_from("<II", entry, 8)
        if kind == 0 and start == count == 0: continue
        if kind in (0, 0xEE) or not start or not count: raise ValueError("Unsupported or empty MBR entry")
        offset, size = start * 512, count * 512
        checked_span(offset, size, total)
        if any(offset < p["offset"] + p["nbytes"] and p["offset"] < offset + size for p in partitions):
            raise ValueError("Overlapping MBR partitions")
        partitions.append({"index": i + 1, "type_hex": f"0x{kind:02X}", "start_lba": start, "sectors": count, "offset": offset, "nbytes": size})
    return partitions


class Fat16:
    """Restricted FAT16 root reader; unsupported formats fail closed."""
    def __init__(self, read_at, partition):
        self.read_at, self.partition = read_at, partition
        b = self.read(0, 512)
        if b[510:512] != b"\x55\xaa": raise ValueError("Invalid FAT boot sector")
        self.bps = struct.unpack_from("<H", b, 11)[0]
        spc, reserved, fats = b[13], struct.unpack_from("<H", b, 14)[0], b[16]
        entries, total16, fat_sectors = struct.unpack_from("<H", b, 17)[0], struct.unpack_from("<H", b, 19)[0], struct.unpack_from("<H", b, 22)[0]
        total = total16 or struct.unpack_from("<I", b, 32)[0]
        if self.bps not in (512, 1024, 2048, 4096) or spc not in (1, 2, 4, 8, 16, 32, 64, 128) or reserved < 1 or fats not in (1, 2) or not entries or not fat_sectors:
            raise ValueError("Unsupported FAT BPB")
        root_sectors = (entries * 32 + self.bps - 1) // self.bps
        data_sector = reserved + fats * fat_sectors + root_sectors
        self.clusters = (total - data_sector) // spc
        if not 4085 <= self.clusters < 65525: raise ValueError("BPB does not establish FAT16")
        checked_span(0, total * self.bps, partition["nbytes"])
        if fat_sectors * self.bps < (self.clusters + 2) * 2: raise ValueError("FAT too small")
        self.cluster_bytes = self.bps * spc
        self.data_start = data_sector * self.bps
        self.fat = self.read(reserved * self.bps, fat_sectors * self.bps)
        self.fat_copies_equal = True
        if fats == 2:
            self.fat_copies_equal = self.fat == self.read((reserved + fat_sectors) * self.bps, fat_sectors * self.bps)
            if not self.fat_copies_equal: raise ValueError("FAT copies differ")
        directory = self.read((reserved + fats * fat_sectors) * self.bps, entries * 32)
        self.entries = []
        for off in range(0, len(directory), 32):
            row = directory[off:off + 32]
            if row[0] == 0: break
            if row[0] == 0xE5 or row[11] == 0x0F: continue
            name = row[:8].decode("ascii", "replace").rstrip()
            ext = row[8:11].decode("ascii", "replace").rstrip()
            self.entries.append({"short_name": name + ("." + ext if ext else ""), "attribute": row[11], "first_cluster": struct.unpack_from("<H", row, 26)[0], "nbytes": struct.unpack_from("<I", row, 28)[0], "fat_date_raw": struct.unpack_from("<H", row, 24)[0], "fat_time_raw": struct.unpack_from("<H", row, 22)[0]})
        self.bpb = {"filesystem_from_bpb": "FAT16", "bytes_per_sector": self.bps, "sectors_per_cluster": spc, "clusters": self.clusters, "fat_copies_equal": self.fat_copies_equal, "partition_type_hex": partition["type_hex"], "note": "MBR type is a label; FAT type determined from BPB cluster count. DOS timestamps have unknown timezone and are auxiliary only."}

    def read(self, offset, count):
        checked_span(offset, count, self.partition["nbytes"])
        raw = self.read_at(self.partition["offset"] + offset, count)
        if len(raw) != count: raise ValueError("Truncated image read")
        return raw

    def extract(self, name, limit=16 * 1024 * 1024):
        hits = [e for e in self.entries if e["short_name"] == name]
        if len(hits) != 1 or hits[0]["attribute"] & 0x18: raise ValueError("Missing, duplicate or nonregular FAT root file")
        e = hits[0]
        if not 0 < e["nbytes"] <= limit: raise ValueError("File outside extraction size limit")
        needed = (e["nbytes"] + self.cluster_bytes - 1) // self.cluster_bytes
        cluster, seen, chunks = e["first_cluster"], set(), []
        for index in range(needed):
            if not 2 <= cluster < self.clusters + 2 or cluster in seen: raise ValueError("Invalid/cyclic FAT chain")
            seen.add(cluster)
            chunks.append(self.read(self.data_start + (cluster - 2) * self.cluster_bytes, self.cluster_bytes))
            cluster = struct.unpack_from("<H", self.fat, cluster * 2)[0]
            if index < needed - 1 and cluster >= 0xFFF8: raise ValueError("Truncated FAT chain")
        if cluster < 0xFFF8: raise ValueError("File chain does not end at expected allocation")
        data = b"".join(chunks)[:e["nbytes"]]
        return data, {**e, "clusters_read": needed, "sha256": hashlib.sha256(data).hexdigest()}


def parse_zynq_boot(raw):
    """UG1283/UG821 Zynq-7000 headers; checksum is structural, not authenticity."""
    if len(raw) < 0x8A0: raise ValueError("BOOT image too short")
    u32 = lambda offset: struct.unpack_from("<I", raw, offset)[0]
    if u32(0x20) != 0xAA995566 or u32(0x24) != 0x584C4E58: raise ValueError("Not a Zynq-7000 boot header")
    if sum(struct.unpack_from("<11I", raw, 0x20)) & 0xFFFFFFFF != 0xFFFFFFFF: raise ValueError("Boot header checksum mismatch")
    source, fsbl_size = u32(0x30), u32(0x34)
    checked_span(source, u32(0x40), len(raw))
    if not fsbl_size or fsbl_size > u32(0x40): raise ValueError("Invalid FSBL length")
    iht, pht = u32(0x98), u32(0x9C)
    if min(iht, pht) < 0x8A0 or iht % 4 or pht % 4: raise ValueError("Invalid header table address")
    checked_span(iht, 64, len(raw))
    version, image_count, first_pht_word, first_ih_word = struct.unpack_from("<4I", raw, iht)
    if version not in (0x01010000, 0x01020000) or not 1 <= image_count <= 128 or first_pht_word * 4 != pht:
        raise ValueError("Unsupported or inconsistent image header table")
    image_headers, visited, next_ih = [], set(), first_ih_word * 4
    for _ in range(image_count):
        if next_ih in visited or next_ih < 0x8A0: raise ValueError("Cyclic/invalid image header")
        visited.add(next_ih)
        checked_span(next_ih, 64, len(raw))
        link, part_link, reserved, count = struct.unpack_from("<4I", raw, next_ih)
        if reserved or not 1 <= count <= 128: raise ValueError("Invalid image partition count")
        name = b""
        for pos in range(next_ih + 16, min(next_ih + 4096, len(raw)), 4):
            word = raw[pos:pos + 4]
            if word == b"\x00" * 4: break
            name += word[::-1]
        else: raise ValueError("Unterminated image name")
        image_headers.append({"header_offset": next_ih, "first_partition_header": part_link * 4, "partition_count": count, "image_name": name.split(b"\x00", 1)[0].decode("ascii", "replace")})
        next_ih = link * 4
    if next_ih: raise ValueError("Image header list longer than declared")
    partitions = []
    for i in range(129):
        off = pht + i * 64
        checked_span(off, 64, len(raw))
        words = struct.unpack_from("<16I", raw, off)
        if sum(words) & 0xFFFFFFFF != 0xFFFFFFFF: raise ValueError("Partition header checksum mismatch")
        if not any(words[:15]): break
        if not words[0] or not words[1] or words[2] < words[0]: raise ValueError("Invalid partition length")
        begin, count = words[5] * 4, words[2] * 4
        checked_span(begin, count, len(raw))
        if any(begin < p["data_offset"] + p["total_nbytes"] and p["data_offset"] < begin + count for p in partitions):
            raise ValueError("Overlapping partition data")
        names = [h["image_name"] for h in image_headers if h["first_partition_header"] <= off < h["first_partition_header"] + h["partition_count"] * 64]
        if len(names) != 1: raise ValueError("Partition image ownership ambiguous")
        partitions.append({"index": i, "header_offset": off, "image_name": names[0], "data_offset": begin, "data_nbytes": words[0] * 4, "unpacked_nbytes": words[1] * 4, "total_nbytes": count, "load_address": f"0x{words[3]:08X}", "execution_address": f"0x{words[4]:08X}", "attributes_hex": f"0x{words[6]:08X}", "checksum_valid": True, "sha256_total_partition": hashlib.sha256(raw[begin:begin + count]).hexdigest()})
    else: raise ValueError("No partition terminator")
    if sum(h["partition_count"] for h in image_headers) != len(partitions) or not partitions or partitions[0]["data_offset"] != source:
        raise ValueError("FSBL/header partition correspondence failed")
    pairs = [{"index": i, "address": f"0x{a:08X}", "value": f"0x{v:08X}"} for i in range(256) for a, v in [struct.unpack_from("<II", raw, 0xA0 + i * 8)] if a != 0xFFFFFFFF]
    return {"boot_header_checksum_valid": True, "key_source_hex": f"0x{u32(0x28):08X}", "header_version_hex": f"0x{u32(0x2C):08X}", "fsbl_source_offset": source, "fsbl_image_nbytes": fsbl_size, "fsbl_total_nbytes": u32(0x40), "fsbl_load_address": f"0x{u32(0x38):08X}", "fsbl_entry_address": f"0x{u32(0x3C):08X}", "image_header_table_offset": iht, "partition_header_table_offset": pht, "image_headers": image_headers, "partitions": partitions, "bootrom_register_initialization_pairs": pairs, "authenticity": "Checksums detect structural corruption only; no signature or source-build identity verification", "execution_evidence": "NONE: static data only"}
