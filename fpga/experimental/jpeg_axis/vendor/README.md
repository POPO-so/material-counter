# Pinned JPEG decoder subset

The five files under `jpegDec/` are verbatim copies from `C:/w/mc-jpeg-libs-20261101/codec/L2/include/hw/jpegDec/` at commit `1204c74cf86ff1e54e0933849328cb9ef7ae7fa6`. `LICENSE.txt` is copied from that codec checkout. Their per-file SHA-256 values are recorded in `SOURCE_MANIFEST.json`.

The pinned `XAcc_jpegdecoder.hpp` includes `utils/x_hls_utils.h`. That file is not part of the Apache-licensed vendored subset: it remains an external dependency from the installed AMD Vitis 2026.1 include tree at `C:/AMDDesignTools/2026.1/Vitis/include/utils/x_hls_utils.h`. Its path and SHA-256 are recorded under `external_dependencies` in the manifest. Build configurations must add `-I C:/AMDDesignTools/2026.1/Vitis/include`. Vitis-provided base headers also remain toolchain includes.

The wrapper is the only code intended to adapt these vendor headers. Do not edit the copied decoder implementation in this experiment.
