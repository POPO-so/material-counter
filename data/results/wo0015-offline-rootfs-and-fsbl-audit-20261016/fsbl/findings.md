# WO-0015 offline FSBL, table, DTB, and U-Boot audit

## Scope and inputs

This is a static host-side examination of the previously extracted BOOT.BIN partitions. No board, SSH, MMIO, target execution, or filesystem mount was used. The only generated files are in this `fsbl/` directory plus `data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl-disassembly-and-dtb-audit.json`. Existing evidence was treated as read-only. The old finalizer was not run.

The FSBL payload is 98,320 bytes, SHA-256 `16081243af5ed1d5b908b0c038c13c3e205c131b31d538690786a530dba82161`. BOOT.BIN calls it `zynq_fsbl.elf`, but the extracted bytes begin with ARM branch instructions and do not have ELF magic. The label therefore does not establish an ELF container, source identity, symbol table, or exact build provenance. The partition header reports load and entry address zero and a valid structural checksum; the existing BOOT analysis explicitly reports no signature/source-build identity verification and no execution evidence.

Other static partition facts are: U-Boot payload 1,041,824 bytes at `0x04000000`, SHA-256 `0821d755f1941d9603065b1be0f687da69d4d791e50d525bdb20a8cc8fc3fed1`; DTB payload 19,740 bytes at `0x00100000`, SHA-256 `3f16c67252ed274eaa2bb7ab6500a820a2fe88fd0352e9bba1ab919defac1798`. Their BootGen attributes are `0x10` and `0x12`; FSBL partition attributes are `0x10`. The BootGen report contains no BootROM register-initialization pairs.

The full ARM disassembly is [partition-00.arm-base0.disasm.txt](partition-00.arm-base0.disasm.txt), produced as raw ARM code with GNU `arm-none-eabi-objdump` 2.32.0.20190204 (`-D -b binary -marm --adjust-vma=0`). Addresses in that file are raw offsets because the selected base is zero. The complete decoder output, including every command argument, address, mask, and value, is [decoded-tables.json](decoded-tables.json). A readable, complete tree rendering is [system.dts](system.dts); serial/MAC identity property values would be redacted if present.

## PS7 command parser and decoded tables

The machine code at `0x2b10` loads a 32-bit command from the table pointer. `0x2b14` extracts the low nibble as argument count (`command & 0xF`); `0x2b18` shifts right four for the opcode. The dispatch words at `0x2b40`–`0x2b50` map opcodes 1–5 to handlers `0x2c28`, `0x2c18`, `0x2bec`, `0x2bb4`, and `0x2b60` respectively. This encoding and dispatch were used by the host decoder; the decoder rejects unknown opcodes, mismatched argument counts, truncated operations, missing EXIT words, and unaligned offsets.

Machine-code effects:

- Opcode 1 stores zero to its table-supplied address (`STR` at `0x2c30`/`0x2c34`).
- Opcode 2 stores its value argument through its table-supplied address (`STR` at `0x2c20`).
- Opcode 3 reads the table-supplied address, calculates `(old & ~mask) | (value & mask)`, and stores the result (`EOR`/`AND`/`EOR` at `0x2bf8`–`0x2c08`). These are indirect target writes, but each table candidate’s address/mask/value is fully enumerated in the output JSON.
- Opcode 4 compares bits by reading its address and mask; its handler has no store.
- Opcode 5 waits on a table-supplied timer/counter address and delay count. The handler also programs its fixed timer registers at `0xF8F00208`, `0xF8F00200`, and `0xF8F00204` while polling.

The audit decoded 21 candidate tables and 675 operations: 15 PS7 initialization tables (five each for silicon revision fields 0, 1, and 2-or-later), three post-config candidates, and three revision-adjacent candidates selected by code at `0x2c88`. These are candidates for different selector values; the counts do not claim all candidates execute. The PS7 table selector at `0x2cc8` reads `0xF8007080` and uses bits `[31:28]` to choose five table pointers. The adjacent selector at `0x2c88` uses the same field and selects one of three 3-WRITE tables. Its semantic label is unknown.

The parser's indirect writes bind their effective target directly to the address argument in each command: opcode 2 loads the address/value args then stores through the address pointer at `0x2c20`; opcode 3 loads address/mask/value and stores the calculated result through that address at `0x2c08`. `decoded-tables.json` contains every candidate operation with table start, command offset, complete args, and derived fields. The focused target audit reports `0xF8009000`: 0 decoded table address-argument hits; `0xF8009014`: 0 hits; `0xF8000240`: 3 hits, one in each post-config candidate at command offsets `0x10794`, `0x107D0`, and `0x1080C`, each `MASKWRITE` mask `0xFFFFFFFF`, value zero. The three are alternative candidates, not three observed writes. Bit 21 at `0xF8000240` is not HP1 status.

At `0x2c48`, the post-config selector also reads `0xF8007080[31:28]`: zero selects `0x10778`, one selects `0x107b4`, and all other values select `0x107f0`. All three 60-byte candidates decode identically, four operations followed by EXIT:

| Operation | Address | Mask | Value |
|---|---:|---:|---:|
| WRITE | `0xF8000008` | — | `0x0000DF0D` |
| MASKWRITE | `0xF8000900` | `0x0000000F` | `0x0000000F` |
| MASKWRITE | `0xF8000240` | `0xFFFFFFFF` | `0x00000000` |
| WRITE | `0xF8000004` | — | `0x0000767B` |

The address `0xF8000240` and its cleared bit 21 are not evidence of HP1 state. Across all decoded table candidates, there are 654 WRITE/WRITE_ZERO/MASKWRITE operations; no decoded direct table address argument equals `0xF8009000` or `0xF8009014`. This is bounded to the command tables decoded here; it does not prove that unrelated code or other runtime pointer paths cannot write registers.

## `0x193ED` flag and handoff control flow

The facts below come from instruction words in the raw image and the local BootGen partition records. Source names are deliberately separated from those facts: `FsblHandoff`, `LoadBootImage`, `BitstreamFlag`, and the meanings of the partition masks are **inferences** from the installed 2026.1 SDK reference excerpts in `init-chain/evidence.json`. That reference is explicitly marked as a template, not established as this image’s source. The extracted payload has no ELF symbols.

1. Reset address `0x0` branches to `0x12c`; initialization branches from `0x398` to `0x4438`. At `0x4438`, execution calls `0x71ac`, then the loops at `0x4440`–`0x4454` and `0x4458`–`0x446c` store zero words over `[0x19040, 0x1b400)`. This covers byte address `0x193ED` (inside word `0x193EC`).
2. The routine beginning at `0xAA8` constructs `r4=0x1905C`. At `0xAC0`, `STRB r3,[r4,#0x391]` stores the zero already held in `r3`, clearing byte `0x193ED` again before its partition scan. The caller at `0x102A4` calls `0xAA8`, then `0x102A8` calls the routine at `0x12CC`.
3. In the partition scan, `0xB64` loads the partition-header field `[r9,#0x58]` into `fp`. At `0xB80`, `TST fp,#0x20` checks bit `0x20`; when clear, `0xB94 BEQ 0xBB4` branches around the PL block. Only the PL block reaches `0xBAC`, where `STRB r2,[r4,#0x391]` writes byte `1` to `0x193ED`. The non-PL path at `0xBB4` checks bit `0x10` and stores other state bytes at `0x193EB`, `0x193EE`, and `0x193EC`; it does not store to `0x193ED`.
4. The BOOT header reports attributes `0x10`, `0x10`, and `0x12` for the three image partitions. All have bit `0x20` clear. The machine-code condition plus those static header values therefore support this **normal partition-scan inference**: beginning from the stated reset path, the flag is zeroed, the three listed partitions take the non-PL branch, and that scan does not set `0x193ED` to one. `0x20` as the PL-image mask and `0x10` as PS-image mask are supported by the installed SDK template, not by source identity proof.
5. At `0x12CC`/`0x12D4`, code constructs `0x193ED`; `0x12DC` loads its byte and `0x12E0` compares it with zero. `0x12E4 BNE 0x1350` reaches the post-config call (`0x1350 BL 0x2C48`) only when the flag is nonzero. With the flag zero, that branch is not taken and the handoff-time post-config call is skipped. For a nonzero handoff address, the zero-flag path later calls `0x103E0` at `0x1334`; the helper uses `BX LR` at `0x10400` to transfer through the supplied address. A zero address follows a separate branch at `0x1320`. The BOOT header’s U-Boot entry address is `0x04000000`, but static evidence does not prove what the partition loader returned at runtime.

Three direct instruction references to `0x193ED` appear in the disassembly: two byte stores (reset at `0xAC0`, PL-only store at `0xBAC`) and one byte load (`0x12DC`). This establishes the identified normal partition path, rather than turning a missing string or literal search into a proof that no possible alias exists. Aliased writes from unrelated control paths and alternate/mutated boot inputs remain outside what is proven here.

There is a separate early JTAG-mode branch. The dispatch at `0x101C4` masks the boot-mode value to three bits; selector zero maps to `0x102E8`. There, `0x102F0` reads `0xF800700C`, `0x102F4` tests bit `0x4`, and `0x102F8 BNE 0x103A4` enters a block that calls `0x2C48`; a clear bit skips that call. The SDK template associates selector zero with JTAG and the tested bit with `PCFG_DONE`. Therefore the no-PL partition scan rules out the handoff-time call on its normal path, but does not rule out an earlier JTAG preloaded-bitstream call. Actual boot mode and `PCFG_DONE` state are unknown because there is no execution or hardware evidence.

## DTB and U-Boot evidence

The DTB parser checked the FDT header and all structure tokens, then rendered 98 nodes and 478 properties to `system.dts`; the FDT header's total size is 19,738 bytes and the partition has two trailing padding bytes. Numeric cells are represented as big-endian 32-bit cells, strings remain quoted string lists, and unclassified non-cell values remain byte arrays. `dtc` was not available for an independent compiler round-trip; the parser-level checks and real-partition tree test passed. Identity-like properties (`serial-number`, MAC address variants) are redacted by property name if encountered.

The result JSON includes paths and selected properties for the relevant nodes: `/fpga-region` (`compatible = "fpga-region"`, `fpga-mgr = <0x3>`), whose phandle resolves to `/axi/devcfg@f8007000` (`compatible = "xlnx,zynq-devcfg-1.0"`, `reg = <0xf8007000 0x100>`), and `/axi/slcr@f8000000` (`compatible = "xlnx,zynq-slcr", "syscon", "simple-mfd"`, `reg = <0xf8000000 0x1000>`). No property/node name or printable value matched `afi`/`xlnx,afi-fpga`. That DTB naming result does not establish that software cannot write AFI registers.

The U-Boot partition was scanned only as NUL-delimited strings for an exact boot/FPGA/AFI allowlist; [uboot-boot-env-allowlist.json](uboot-boot-env-allowlist.json) records selected offsets and values, not a full `strings` dump. Examples include `bootcmd=run distro_bootcmd`, `bootdelay=2`, `baudrate=115200`, `loadaddr=0x0`, and a `boot_targets` list beginning with `jtag mmc0 mmc1 qspi`. This does not prove that U-Boot selected these values or that they were not changed at runtime.

The prior `init-chain/boot-script-audit.json` marks the script CRCs valid but `executed: false`; its static script can import `/uEnv.txt`, run `uenvcmd`, load alternate image/DTB files, and apply an optional overlay. Those are source recipe possibilities, not historical execution evidence. The image audit contains no BootROM register-init pairs. Decoded FSBL tables explicitly expose their direct register-address operations, but this work does not perform a complete U-Boot code-level MMIO dataflow analysis. Whether U-Boot or another path performs other direct or indirect register initialization is therefore **UNKNOWN**.

## Local source-file search and verification

A filename-only search of `C:\fpga` and `C:\Users\POP\Desktop\FPGA赛` looked for FSBL ELF, BIF, MAP, and serial/UART/boot-log filenames; restricted-raw, REVISION-named, and credential-named paths were excluded. No original FSBL ELF, BIF, or serial/UART/boot log filename was found. Six matching `.map` files were unrelated Vivado incremental-synthesis maps. No matching file contents were opened.

The decoder test log has an initial intentional red phase in [table-decoder-red.txt](table-decoder-red.txt), followed by a green phase in [table-decoder-green.txt](table-decoder-green.txt). The final focused suite includes synthetic operation/error cases, all 21 candidate tables from the real payload, identity across the three post-config tables, and DTB structural/property-format checks. Machine-readable test results are in [table-decoder-tests.xml](table-decoder-tests.xml).

### Additional read-only archive search

The follow-up search enumerated only ZIP central-directory names and metadata for `F:/FPGA/backups/pynq_counter_lab.xpr.zip` (13,921,750 bytes; 472 members), `D:/Downloads/Pynq-Z2-XDC-and-Board-File-main.zip` (173,914 bytes; 4 members), and `D:/Downloads/pynq-z2 (1).zip` (165,993 bytes; 6 members). It found no FSBL ELF/BIF/MAP or serial/UART/boot-log member filename. A non-recursive filename-only listing of direct children of `F:/FPGA/backups` found the named project ZIP as the sole archive candidate and no separate FSBL or boot-log filename. No archive member contents were read, no archive was extracted, and no archive hash was computed. Sensitive-looking member names, if any, are represented only by an excluded-name count (zero in these three archives). These results are bounded to the three named archives and direct filenames in `F:/FPGA/backups`; they do not establish global absence. See [archive-search.json](archive-search.json).
