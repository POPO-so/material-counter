"""Safe, read-only status snapshot for the standalone G2 RLE pipeline."""

READ_OFFSETS = (
    0x04,
    0x08,
    0x0C,
    0x10,
    0x14,
    0x18,
    0x1C,
    0x20,
    0x24,
    0x28,
    0x2C,
    0x30,
    0x38,
)

_REGISTER_NAMES = {
    0x04: "signature",
    0x08: "state",
    0x0C: "actual_bytes",
    0x10: "frame_id",
    0x14: "params_version",
    0x18: "roi_start",
    0x1C: "roi_end",
    0x20: "threshold",
    0x24: "offset",
    0x28: "morph",
    0x2C: "geometry",
    0x30: "max_runs",
    0x38: "packet_code",
}


def read_pipeline(mmio) -> dict:
    """Read and validate only documented G2 pipeline registers.

    The returned fields each retain the RTL register offset and raw value so
    callers can trace the snapshot back to the register map.
    """
    values = {offset: mmio.read(offset) for offset in READ_OFFSETS}

    if values[0x04] != 0x31454C52:
        raise RuntimeError("pipeline signature mismatch")
    if values[0x2C] != (400 << 16) | 640:
        raise RuntimeError("pipeline geometry mismatch")
    if values[0x30] != 16384:
        raise RuntimeError("pipeline max_runs mismatch")

    state_value = values[0x08]
    busy = bool(state_value & 0x1)
    done_seen = bool(state_value & 0x2)
    error = bool(state_value & 0x4)
    if busy:
        raise RuntimeError("pipeline busy state is set")
    if error:
        raise RuntimeError("pipeline error state is set")

    result = {
        _REGISTER_NAMES[offset]: {"offset": offset, "value": values[offset]}
        for offset in READ_OFFSETS
    }
    result["state"].update(
        busy=busy,
        done_seen=done_seen,
        error=error,
    )
    return result
