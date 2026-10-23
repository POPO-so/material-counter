NOT_APPROVED_DO_NOT_EXECUTE

# WO-0015 UART Read-Only Capture Design

## Scope

This artifact defines a software facade for a receive-only UART log capture and a pure parser for an already-captured byte string. It does not open or discover a device, configure a driver, start a listener, access SSH, or touch a board. A caller must inject an already-created abstract receive-only transport:

```python
capture = ReadOnlySerialCapture(existing_receive_only_transport)
chunk = capture.read(256)
```

The example starts from an existing object; it is not a serial-port launcher. The module imports no serial-driver package and supplies no opener or command-line runner.

## Fixed connection contract

`ReadOnlySerialCapture.config` returns the immutable contract:

| Setting | Value |
| --- | ---: |
| Baud rate | 115200 |
| Data bits | 8 |
| Parity | N |
| Stop bits | 1 |
| RTS/CTS | disabled |
| XON/XOFF | disabled |
| DSR/DTR | disabled |

The facade reports this contract but does not apply it to the injected transport. Its only delegated operation is read(size), and every result must be bytes with no more bytes than requested. The default request is 4096 bytes; accepted sizes are 1 through 65536 inclusive. It exposes no public transport property and does not forward unknown names. Normal access to the private backend slot is denied. This Python facade is not a hostile-code sandbox: callers can bypass attribute hooks with object.__getattribute__, and the owner may still hold the originally injected transport.

Calls to write, writelines, send_break, common camel-case aliases, set_input_flow_control, set_output_flow_control, apply_settings, open, close, fileno, and any attribute assignment raise:

```text
PermissionError: UART TX is strictly prohibited in read-only capture mode
```

The config object is frozen. Missing transport attributes remain unavailable. The facade does not open or close the injected handle; an approved owner handles that lifecycle outside this API.

## Offline parser output

`parse_uart_log(raw: bytes)` performs no I/O and accepts only bytes. It returns an `interpretation` note plus observations for:

| Field | Value when observed |
| --- | --- |
| `fsbl` | Matching FSBL / First Stage Boot Loader line |
| `silicon_revision` | Text after the Silicon Revision label |
| `ps7_post_config` | Matching line |
| `uboot_version` | Version text after U-Boot |
| `uboot_2024_01_candidate_match` | Boolean match for the 2024.01 version text |
| `bootcmd` | Value from a `bootcmd=` line |
| `boot_scr_names` | Unique script basenames ending in `.scr` |
| `boot_scr_source_lines` | Full matching load/source lines, preserving any path such as `/boot/BOOT.SCR` |
| `load_file_names` | Unique recognized filenames on load/read/source lines |
| Extensionless boot files | uImage, zImage, Image, and initrd are recognized on load/read/source lines |
| `linux_version` | Linux version token |
| `root_uuid` | UUID from a `root=UUID=...` argument |
| `root_uuid_expected_match` | Boolean comparison with `63b8b04d-af08-419a-a9dc-16f374e283dc` |
| `fpga_manager_base_bit_events` | Matching lines containing both `fpga_manager` and `base.bit` |

Each observation has `status` and `value`. Status is `OBSERVED_IN_UART_LOG` when matching text exists and `NOT_OBSERVED` otherwise. A missing value is returned as `null`; for the U-Boot and UUID match fields, a boolean is produced only when the corresponding version or UUID was observed.

These are textual observations from the supplied bytes. They do not prove live board identity, active runtime state, that a bitstream was successfully configured, or that a named root filesystem is mounted. `NOT_OBSERVED` means the pattern was absent from this input; it does not mean the condition was disproven.

## Physical safety boundary

A Python facade cannot guarantee that a USB-UART driver or adapter will avoid DTR/RTS glitches while its device handle is first opened. A Python facade is not a hostile-code sandbox: object.__getattribute__ can bypass attribute hooks, and the creator may still hold the originally injected transport. Physical TX/control isolation remains mandatory. This wrapper deliberately has no opening path, but an external transport may already have been created before injection.

Software read-only behavior also cannot establish a whole-SD zero-write guarantee. That requires independent physical or system-level controls and evidence. Physical TX/control isolation and separate approval for any future device connection or live capture are required before board-side use. No board interaction is part of this design or its offline tests.




