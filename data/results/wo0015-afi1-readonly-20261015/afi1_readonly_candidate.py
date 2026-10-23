"""Review candidate only. No transport or live precondition verifier is supplied.

Running this file performs no hardware access. A future caller must provide an
independently audited live verifier; cached JSON/old measurements are not proof.
No reset register is read here: its interpretation/authorization remains open.
"""
import datetime
import mmap
import os
import struct

VERSION = "wo0015-afi1-readonly-candidate-v1"
REGISTERS = (("AFI1_WRCHAN_CTRL", 0xF8009014),
             ("AFI1_RDCHAN_CTRL", 0xF8009000))
REQUIRED = ("current_board_identity", "locked_artifacts", "loaded_design_identity",
            "hp1_mapping", "no_conflicting_owner", "runtime_environment",
            "reset_documentation_resolved", "current_reset_deasserted",
            "safe_access_confirmed")


def read_once(live_verifier=None):
    """No retry, no writable fd/map, exactly two AFI reads after verified gates.

    This core does not authenticate the verifier. It is NOT cleared for board
    execution until that missing trusted live verifier has been reviewed.
    """
    result = {"version": VERSION, "status": "STOPPED_PRECONDITION_UNVERIFIED",
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "registers": [], "hardware_access_started": False}
    if live_verifier is None:
        result["reason"] = "No audited live precondition verifier supplied"
        return result
    try:
        checks = live_verifier()
        missing = [name for name in REQUIRED if checks.get(name) is not True]
        if missing:
            result["reason"] = "Unverified prerequisites: " + ", ".join(missing)
            return result
        # Fresh identity/reset provenance must be retained by the future verifier.
        result["preconditions"] = checks
        fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
        try:
            with mmap.mmap(fd, mmap.PAGESIZE, flags=mmap.MAP_SHARED,
                           prot=mmap.PROT_READ, offset=0xF8009000) as page:
                for name, address in REGISTERS:
                    result["hardware_access_started"] = True
                    raw = struct.unpack("<I", page[address - 0xF8009000:
                                                  address - 0xF8009000 + 4])[0]
                    result["registers"].append({
                        "name": name, "address": f"0x{address:08X}",
                        "raw_hex": f"0x{raw:08X}", "32BitEn": raw & 1,
                        "width_bits": 32 if raw & 1 else 64,
                        "read_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        finally:
            os.close(fd)
        result["status"] = "READ_COMPLETE"
        result["static_32_consistent"] = all(x["width_bits"] == 32 for x in result["registers"])
    except Exception as exc:
        result["status"] = "FAIL_STOPPED"
        result["reason"] = f"{type(exc).__name__}: {exc}"
    result["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result
