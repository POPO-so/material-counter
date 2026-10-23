from __future__ import annotations

import pytest

from uart_readonly import (
    CONFIG,
    EXPECTED_ROOT_UUID,
    ReadOnlySerialCapture,
    parse_uart_log,
)


class MockReceiveOnlyTransport:
    def __init__(self, result: bytes = b"boot\r\n") -> None:
        self.result = result
        self.read_calls: list[int] = []
        self.transmit_calls: list[tuple[str, object]] = []
        self.diagnostic_value = "must not leak through the facade"

    def read(self, size: int = 1) -> bytes:
        self.read_calls.append(size)
        return self.result

    def write(self, data: object) -> int:
        self.transmit_calls.append(("write", data))
        return len(data)  # type: ignore[arg-type]

    def writelines(self, lines: object) -> None:
        self.transmit_calls.append(("writelines", lines))

    def send_break(self, duration: float = 0.25) -> None:
        self.transmit_calls.append(("send_break", duration))


def assert_observation(result: dict, name: str, status: str, value: object = None) -> None:
    assert result[name] == {"status": status, "value": value}


def test_capture_reads_only_bytes_from_injected_transport() -> None:
    transport = MockReceiveOnlyTransport(b"prompt> ")
    capture = ReadOnlySerialCapture(transport)

    assert capture.read(64) == b"prompt> "
    assert transport.read_calls == [64]
    assert transport.transmit_calls == []
    assert capture.config == CONFIG


def test_capture_uses_bounded_positive_default_read_size() -> None:
    transport = MockReceiveOnlyTransport()
    capture = ReadOnlySerialCapture(transport)

    assert capture.read() == b"boot\r\n"
    assert len(transport.read_calls) == 1
    assert 1 <= transport.read_calls[0] <= 65536


@pytest.mark.parametrize("size", [0, -1, 65537])
def test_capture_rejects_out_of_range_read_sizes_before_transport_call(size: int) -> None:
    transport = MockReceiveOnlyTransport()
    capture = ReadOnlySerialCapture(transport)

    with pytest.raises(ValueError, match="read size must be between 1 and 65536 bytes"):
        capture.read(size)

    assert transport.read_calls == []


@pytest.mark.parametrize("size", [True, "8", None])
def test_capture_rejects_non_integer_read_sizes_before_transport_call(size: object) -> None:
    transport = MockReceiveOnlyTransport()
    capture = ReadOnlySerialCapture(transport)

    with pytest.raises(TypeError, match="read size must be an integer"):
        capture.read(size)  # type: ignore[arg-type]

    assert transport.read_calls == []

def test_capture_rejects_non_bytes_transport_reads() -> None:
    transport = MockReceiveOnlyTransport("not bytes")  # type: ignore[arg-type]
    capture = ReadOnlySerialCapture(transport)

    with pytest.raises(TypeError, match=r"read\(\) must return bytes"):
        capture.read(4)



def test_capture_rejects_transport_reads_larger_than_requested() -> None:
    transport = MockReceiveOnlyTransport(b"longer than requested")
    capture = ReadOnlySerialCapture(transport)

    with pytest.raises(ValueError, match=r"read\(\) returned more bytes than requested"):
        capture.read(4)

    assert transport.read_calls == [4]


def test_private_backend_attribute_is_denied() -> None:
    capture = ReadOnlySerialCapture(MockReceiveOnlyTransport())

    with pytest.raises(
        PermissionError,
        match="^UART TX is strictly prohibited in read-only capture mode$",
    ):
        _ = capture._ReadOnlySerialCapture__transport

@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("write", (b"TX",)),
        ("writelines", ([b"TX"],)),
        ("send_break", ()),
        ("sendBreak", ()),
        ("setDTR", (True,)),
        ("setRTS", (True,)),
        ("setBreak", (True,)),
        ("set_input_flow_control", (True,)),
        ("set_output_flow_control", (True,)),
        ("apply_settings", ({"baudrate": 9600},)),
        ("open", ()),
        ("close", ()),
        ("fileno", ()),
    ],
)
def test_transmit_and_control_methods_are_denied(method_name: str, args: tuple) -> None:
    transport = MockReceiveOnlyTransport()
    capture = ReadOnlySerialCapture(transport)

    with pytest.raises(
        PermissionError,
        match="^UART TX is strictly prohibited in read-only capture mode$",
    ):
        getattr(capture, method_name)(*args)

    assert transport.transmit_calls == []


@pytest.mark.parametrize("name", ["dtr", "rts", "baudrate", "rtscts", "xonxoff", "dsrdtr"])
def test_settings_and_control_assignment_is_denied(name: str) -> None:
    capture = ReadOnlySerialCapture(MockReceiveOnlyTransport())

    with pytest.raises(
        PermissionError,
        match="^UART TX is strictly prohibited in read-only capture mode$",
    ):
        setattr(capture, name, True)


def test_facade_is_immutable_and_does_not_expose_or_forward_transport() -> None:
    transport = MockReceiveOnlyTransport()
    capture = ReadOnlySerialCapture(transport)

    assert not hasattr(capture, "transport")
    assert not hasattr(capture, "diagnostic_value")
    with pytest.raises(AttributeError):
        _ = capture.diagnostic_value
    with pytest.raises(AttributeError):
        _ = capture.transport

    with pytest.raises(
        PermissionError,
        match="^UART TX is strictly prohibited in read-only capture mode$",
    ):
        capture.extra = "mutation"

    assert transport.transmit_calls == []


def test_config_is_fixed_to_read_only_115200_8n1_contract() -> None:
    assert CONFIG.baudrate == 115200
    assert CONFIG.bytesize == 8
    assert CONFIG.parity == "N"
    assert CONFIG.stopbits == 1
    assert CONFIG.rtscts is False
    assert CONFIG.xonxoff is False
    assert CONFIG.dsrdtr is False


def test_parser_reports_observed_boot_and_runtime_log_candidates() -> None:
    raw = (
        b"Xilinx Zynq MP First Stage Boot Loader\r\n"
        b"Silicon Revision 1.0\r\n"
        b"ps7_post_config: done\r\n"
        b"U-Boot 2024.01 (Jan 01 2024 - 00:00:00)\r\n"
        b"bootcmd=run distro_bootcmd; run boot_targets\r\n"
        b"Loading /boot/BOOT.SCR from mmc 0:1\r\n"
        b"load mmc 0:1 0x10000000 image.ub\r\n"
        b"Linux version 6.6.0-xilinx\r\n"
        b"Kernel command line: root=UUID="
        + EXPECTED_ROOT_UUID.encode("ascii")
        + b" rw rootwait\r\n"
        b"fpga_manager fpga0: writing base.bit\r\n"
    )

    result = parse_uart_log(raw)

    assert_observation(result, "fsbl", "OBSERVED_IN_UART_LOG", "Xilinx Zynq MP First Stage Boot Loader")
    assert_observation(result, "silicon_revision", "OBSERVED_IN_UART_LOG", "1.0")
    assert_observation(result, "ps7_post_config", "OBSERVED_IN_UART_LOG", "ps7_post_config: done")
    assert_observation(
        result,
        "uboot_version",
        "OBSERVED_IN_UART_LOG",
        "2024.01 (Jan 01 2024 - 00:00:00)",
    )
    assert_observation(result, "uboot_2024_01_candidate_match", "OBSERVED_IN_UART_LOG", True)
    assert_observation(
        result,
        "bootcmd",
        "OBSERVED_IN_UART_LOG",
        "run distro_bootcmd; run boot_targets",
    )
    assert_observation(result, "boot_scr_names", "OBSERVED_IN_UART_LOG", ["BOOT.SCR"])
    assert_observation(result, "boot_scr_source_lines", "OBSERVED_IN_UART_LOG", ["Loading /boot/BOOT.SCR from mmc 0:1"])
    assert_observation(result, "load_file_names", "OBSERVED_IN_UART_LOG", ["BOOT.SCR", "image.ub"])
    assert_observation(result, "linux_version", "OBSERVED_IN_UART_LOG", "6.6.0-xilinx")
    assert_observation(result, "root_uuid", "OBSERVED_IN_UART_LOG", EXPECTED_ROOT_UUID)
    assert_observation(result, "root_uuid_expected_match", "OBSERVED_IN_UART_LOG", True)
    assert_observation(
        result,
        "fpga_manager_base_bit_events",
        "OBSERVED_IN_UART_LOG",
        ["fpga_manager fpga0: writing base.bit"],
    )


def test_parser_recognizes_extensionless_boot_files_on_load_lines() -> None:
    result = parse_uart_log(
        b"load mmc 0:1 0x01000000 uImage zImage Image initrd\n"
    )

    assert_observation(
        result,
        "load_file_names",
        "OBSERVED_IN_UART_LOG",
        ["uImage", "zImage", "Image", "initrd"],
    )

def test_parser_keeps_missing_items_unknown_and_records_mismatches_as_observations() -> None:
    result = parse_uart_log(b"U-Boot 2023.10\nKernel: root=UUID=00000000-0000-0000-0000-000000000000\n")

    assert_observation(result, "fsbl", "NOT_OBSERVED")
    assert_observation(result, "silicon_revision", "NOT_OBSERVED")
    assert_observation(result, "ps7_post_config", "NOT_OBSERVED")
    assert_observation(result, "uboot_2024_01_candidate_match", "OBSERVED_IN_UART_LOG", False)
    assert_observation(result, "bootcmd", "NOT_OBSERVED")
    assert_observation(result, "boot_scr_names", "NOT_OBSERVED")
    assert_observation(result, "boot_scr_source_lines", "NOT_OBSERVED")
    assert_observation(result, "load_file_names", "NOT_OBSERVED")
    assert_observation(result, "linux_version", "NOT_OBSERVED")
    assert_observation(result, "root_uuid_expected_match", "OBSERVED_IN_UART_LOG", False)
    assert_observation(result, "fpga_manager_base_bit_events", "NOT_OBSERVED")


@pytest.mark.parametrize("raw", ["text", bytearray(b"bytes"), None])
def test_parser_requires_bytes_input(raw: object) -> None:
    with pytest.raises(TypeError, match="raw UART log must be bytes"):
        parse_uart_log(raw)  # type: ignore[arg-type]







