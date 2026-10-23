from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


EXPECTED_ROOT_UUID = "63b8b04d-af08-419a-a9dc-16f374e283dc"
DEFAULT_READ_SIZE = 4096
MAX_READ_SIZE = 65536
EXECUTION_STATUS = "NOT_APPROVED_DO_NOT_EXECUTE"
_TX_PROHIBITED = "UART TX is strictly prohibited in read-only capture mode"
_OBSERVED = "OBSERVED_IN_UART_LOG"
_NOT_OBSERVED = "NOT_OBSERVED"


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """The fixed serial-format contract for a future receive-only capture."""

    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    rtscts: bool = False
    xonxoff: bool = False
    dsrdtr: bool = False


CONFIG = CaptureConfig()


def _deny_transmit(*args: Any, **kwargs: Any) -> None:
    raise PermissionError(_TX_PROHIBITED)


class ReadOnlySerialCapture:
    """Read bytes from an already-created receive-only transport.

    This facade never opens, configures, launches, or discovers a transport.
    Only its explicit read method is forwarded. Every other transport
    attribute remains unavailable.
    """

    __slots__ = ("__transport",)

    def __init__(self, transport: object) -> None:
        reader = getattr(transport, "read", None)
        if not callable(reader):
            raise TypeError("transport must provide a callable read() method")
        object.__setattr__(self, "_ReadOnlySerialCapture__transport", transport)

    def __getattribute__(self, name: str) -> Any:
        if name == "_ReadOnlySerialCapture__transport":
            raise PermissionError(_TX_PROHIBITED)
        return object.__getattribute__(self, name)
    @property
    def config(self) -> CaptureConfig:
        return CONFIG

    def read(self, size: int = DEFAULT_READ_SIZE) -> bytes:
        if type(size) is not int:
            raise TypeError("read size must be an integer")
        if not 1 <= size <= MAX_READ_SIZE:
            raise ValueError("read size must be between 1 and 65536 bytes")
        transport = object.__getattribute__(self, "_ReadOnlySerialCapture__transport")
        data = transport.read(size)  # type: ignore[attr-defined]
        if type(data) is not bytes:
            raise TypeError("read() must return bytes")
        if len(data) > size:
            raise ValueError("read() returned more bytes than requested")
        return data
    def __setattr__(self, name: str, value: object) -> None:
        raise PermissionError(_TX_PROHIBITED)

    def __delattr__(self, name: str) -> None:
        raise PermissionError(_TX_PROHIBITED)

    # Common serial-library TX and control names are explicit denial methods.
    # Nothing else is forwarded from the injected transport.
    write = _deny_transmit
    writelines = _deny_transmit
    send_break = _deny_transmit
    sendBreak = _deny_transmit
    setDTR = _deny_transmit
    setRTS = _deny_transmit
    setBreak = _deny_transmit
    setDataTerminalReady = _deny_transmit
    setRequestToSend = _deny_transmit
    setXON = _deny_transmit
    setXOFF = _deny_transmit
    setBaudrate = _deny_transmit
    setByteSize = _deny_transmit
    setParity = _deny_transmit
    setStopbits = _deny_transmit
    setRtsCts = _deny_transmit
    setDsrDtr = _deny_transmit
    flush = _deny_transmit
    flushOutput = _deny_transmit
    reset_output_buffer = _deny_transmit
    resetOutputBuffer = _deny_transmit
    cancel_write = _deny_transmit
    set_input_flow_control = _deny_transmit
    set_output_flow_control = _deny_transmit
    apply_settings = _deny_transmit
    open = _deny_transmit
    close = _deny_transmit
    fileno = _deny_transmit


def _observation(value: object = None, *, observed: bool = True) -> dict[str, object]:
    return {
        "status": _OBSERVED if observed else _NOT_OBSERVED,
        "value": value if observed else None,
    }


def _first_value(lines: list[str], pattern: re.Pattern[str], group: int = 1) -> str | None:
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(group).strip()
    return None


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def parse_uart_log(raw: bytes) -> dict[str, object]:
    """Extract bounded textual observations from captured UART bytes.

    NOT_OBSERVED means a pattern was absent from these input bytes. It does
    not disprove the corresponding device, boot stage, or runtime state.
    """

    if type(raw) is not bytes:
        raise TypeError("raw UART log must be bytes")

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()

    fsbl = next(
        (
            line.strip()
            for line in lines
            if re.search(r"\bFSBL\b|First Stage Boot Loader", line, re.IGNORECASE)
        ),
        None,
    )
    silicon_revision = _first_value(
        lines,
        re.compile(r"\bSilicon\s+Revision\b\s*[:=]?\s*([^\r\n]+)", re.IGNORECASE),
    )
    ps7_post_config = next(
        (line.strip() for line in lines if "ps7_post_config" in line.casefold()),
        None,
    )
    uboot_version = _first_value(
        lines,
        re.compile(r"\bU-Boot(?:\s+(?:SPL|version))?\s+([0-9][^\r\n]*)", re.IGNORECASE),
    )
    bootcmd = _first_value(
        lines,
        re.compile(r"^\s*bootcmd\s*=\s*(.*?)\s*$", re.IGNORECASE),
    )

    boot_scr_names = _unique(
        re.findall(r"(?i)\b([A-Za-z0-9_.-]+\.scr)\b", text)
    )

    boot_scr_source_lines = [
        line.strip()
        for line in lines
        if ".scr" in line.casefold()
        and any(token in line.casefold() for token in ("load", "read", "source"))
    ]
    load_context = re.compile(
        r"\b(?:load|loading|loaded|read|reading|source|fatload|ext4load|tftpboot)\b",
        re.IGNORECASE,
    )
    loadable_name = re.compile(
        r"(?i)(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+[.](?:bin|bit|scr|ub|uimage|dtb|img|itb|elf)|uimage|zimage|image|initrd)(?![A-Za-z0-9_.-])"
    )
    load_file_names = _unique(
        [
            name
            for line in lines
            if load_context.search(line)
            for name in loadable_name.findall(line)
        ]
    )

    linux_version = _first_value(
        lines,
        re.compile(r"\bLinux\s+version\s+([^\s]+)", re.IGNORECASE),
    )
    root_uuid = _first_value(
        lines,
        re.compile(
            r"\broot=UUID=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
            re.IGNORECASE,
        ),
    )
    fpga_events = [
        line.strip()
        for line in lines
        if "fpga_manager" in line.casefold() and "base.bit" in line.casefold()
    ]

    result: dict[str, object] = {
        "interpretation": (
            "UART text observations only; they do not prove live runtime or board identity. "
            "NOT_OBSERVED means absent from the supplied bytes, not disproven."
        ),
        "fsbl": _observation(fsbl, observed=fsbl is not None),
        "silicon_revision": _observation(
            silicon_revision, observed=silicon_revision is not None
        ),
        "ps7_post_config": _observation(
            ps7_post_config, observed=ps7_post_config is not None
        ),
        "uboot_version": _observation(
            uboot_version, observed=uboot_version is not None
        ),
        "uboot_2024_01_candidate_match": _observation(
            bool(re.search(r"(?<!\d)2024\.01(?!\d)", uboot_version))
            if uboot_version is not None
            else None,
            observed=uboot_version is not None,
        ),
        "bootcmd": _observation(bootcmd, observed=bootcmd is not None),
        "boot_scr_names": _observation(
            boot_scr_names if boot_scr_names else None,
            observed=bool(boot_scr_names),
        ),
        "boot_scr_source_lines": _observation(
            boot_scr_source_lines if boot_scr_source_lines else None,
            observed=bool(boot_scr_source_lines),
        ),
        "load_file_names": _observation(
            load_file_names if load_file_names else None,
            observed=bool(load_file_names),
        ),
        "linux_version": _observation(
            linux_version, observed=linux_version is not None
        ),
        "root_uuid": _observation(root_uuid, observed=root_uuid is not None),
        "root_uuid_expected_match": _observation(
            root_uuid.casefold() == EXPECTED_ROOT_UUID
            if root_uuid is not None
            else None,
            observed=root_uuid is not None,
        ),
        "fpga_manager_base_bit_events": _observation(
            fpga_events if fpga_events else None,
            observed=bool(fpga_events),
        ),
    }
    return result







