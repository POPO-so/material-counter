# Prepared AMD documentation clarification — not sent

Target: Zynq-7000 XC7Z020 / PYNQ-Z2; PS HP1 declared 32-bit by a Vivado 2026.1
design. No register modifications, resets, bitstream reloads or DMA are planned
as part of the requested clarification.

1. UG585 v1.10 Appendix B p.1556 and the current AMD FPGA_RST_CTRL field index
   classify bit21 as reserved, with reset value1 and a write-zero requirement.
   AFI_RDCHAN_CTRL / AFI_WRCHAN_CTRL details nevertheless require the associated
   FPGA_RST_CTRL.FPGA_AXDSN_RST field to have been written zero before access.
   What is the intended prerequisite for each production Zynq-7000 silicon
   revision? Is this an outdated named-field reference or a required reserved
   initialization sequence? Please identify the authoritative erratum/section.
2. Vivado's generated PS7 tables label this position as FPGA_AXDS1_RST for
   silicon1.0/2.0 but reserved_FPGA_AXDS1_RST for3.0. Which meanings are valid
   for the actual silicon, and does any read-only status authoritatively prove
   AFI1 register-access safety? We will not interpret a reserved bit as a status.
3. Does a single aligned 32-bit read of FPGA_RST_CTRL at0xF8000240 have any
   read side effects or prerequisites beyond normal PS SLCR accessibility?
   SLCR_LOCKSTA says locked reads remain valid; no unlock/write is proposed.
4. For already-running Linux, what conditions must be verified before reading
   only AFI1_WRCHAN_CTRL at0xF8009014 and RDCHAN_CTRL at0xF8009000? In particular,
   which reset/clock/isolation requirements apply to these controls, and which
   apply only to FIFO-level/debug/data-path registers?

The current board state and silicon revision are not asserted by this request.
No inference from a past successful DMA is being used to waive prerequisites.
