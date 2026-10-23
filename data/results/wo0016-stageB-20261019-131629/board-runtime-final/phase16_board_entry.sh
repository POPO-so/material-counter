#!/usr/bin/env bash
set -eo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Phase 1.6 board entry must run under sudo as root" >&2
  exit 77
fi

ulimit -c 0 || true
for f in /etc/profile.d/*.sh; do
  if [[ -r "$f" ]]; then
    # Some board profiles probe optional files and return nonzero when absent.
    # Source every readable profile while keeping the wrapper fail-closed on
    # its explicit readiness gates below.
    if . "$f"; then
      :
    else
      profile_status=$?
      echo "profile script returned ${profile_status}; continuing after sourcing: $f" >&2
    fi
  fi
done

PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-}"
EXPECTED_BOOT_ID="${2:-}"
if [[ -z "$ACTION" || -z "$EXPECTED_BOOT_ID" ]]; then
  echo "usage: phase16_board_entry.sh {precheck|overlay|smoke3} <authorized-boot-id> ..." >&2
  exit 64
fi
shift 2

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "python3 is unavailable after loading /etc/profile.d/*.sh" >&2
  exit 69
fi
TIMEOUT="$(command -v timeout || true)"
if [[ -z "$TIMEOUT" || ! -x "$TIMEOUT" ]]; then
  echo "timeout is required to bound the read-only PYNQ device probe" >&2
  exit 69
fi

LIVE_BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
if [[ "$LIVE_BOOT_ID" != "$EXPECTED_BOOT_ID" ]]; then
  echo "boot_id mismatch: expected=$EXPECTED_BOOT_ID observed=$LIVE_BOOT_ID" >&2
  exit 70
fi
CMA_FREE_KIB="$(awk '$1 == "CmaFree:" { print $2; exit }' /proc/meminfo)"
echo "boot_id=$LIVE_BOOT_ID"
echo "CmaFree=${CMA_FREE_KIB:-unavailable} KiB"
ps -eo pid,ppid,stat,comm | awk 'NR == 1 || tolower($4) ~ /(python|dma|pynq)/'
if ps -eo pid,ppid,stat,comm | awk 'NR > 1 && $3 ~ /^Z/ && tolower($4) ~ /(python|dma|pynq)/ { print; found = 1 } END { exit found ? 0 : 1 }'; then
  echo "zombie Python/DMA process detected; stopping before hardware access" >&2
  exit 73
fi

if [[ "$ACTION" == "diagnose" ]]; then
  if [[ "$#" -ne 0 ]]; then
    echo "diagnose takes no additional arguments" >&2
    exit 64
  fi
  exec "$PYTHON" "$PACKAGE_DIR/pynq_device_probe.py" --diagnose "$EXPECTED_BOOT_ID"
fi

FPGA_STATE="$(cat /sys/class/fpga_manager/fpga0/state)"
FPGA_FLAGS="$(cat /sys/class/fpga_manager/fpga0/flags)"
if [[ "$FPGA_STATE" != "operating" || "$FPGA_FLAGS" != "0" ]]; then
  echo "FPGA manager gate failed: state=$FPGA_STATE flags=$FPGA_FLAGS" >&2
  exit 72
fi

echo "fpga_manager_state=$FPGA_STATE flags=$FPGA_FLAGS"
echo "python3=$PYTHON"

case "$ACTION" in
  precheck)
    if [[ "$#" -ne 0 ]]; then
      echo "precheck takes no additional arguments" >&2
      exit 64
    fi
    ;;
  overlay)
    if [[ "$#" -ne 2 ]]; then
      echo "overlay requires <wo15-manifest-sha256> <absolute-evidence-json>" >&2
      exit 64
    fi
    ;;
  smoke3)
    if [[ "$#" -ne 4 ]]; then
      echo "smoke3 requires <wo15-manifest-sha256> <stageb-manifest-sha256> <absolute-analysis-json> <absolute-evidence-json>" >&2
      exit 64
    fi
    ;;
  *)
    echo "unsupported Phase 1.6 action: $ACTION" >&2
    exit 64
    ;;
esac

WO15_GUARD_DIR="/var/lib/wo0015-s2mm/boot-${EXPECTED_BOOT_ID}"
WO16_GUARD_DIR="/var/lib/wo0016/boot-${EXPECTED_BOOT_ID}"
WO15_OVERLAY_GUARD="${WO15_GUARD_DIR}/overlay-only-consumed.json"
WO15_OVERLAY_RECEIPT="${WO15_GUARD_DIR}/overlay-only-pass.json"
WO15_TERMINAL_FAILURE="${WO15_GUARD_DIR}/terminal-failure.json"
WO16_SMOKE_GUARD="${WO16_GUARD_DIR}/smoke3-consumed.json"

path_present() {
  [[ -e "$1" || -L "$1" ]]
}

require_absent() {
  local path
  for path in "$@"; do
    if path_present "$path"; then
      echo "refusing to proceed because a one-shot guard/receipt already exists: $path" >&2
      exit 74
    fi
  done
}

require_present() {
  local path
  for path in "$@"; do
    if ! path_present "$path"; then
      echo "required same-boot Stage 1 artifact is absent: $path" >&2
      exit 75
    fi
  done
}

case "$ACTION" in
  precheck)
    require_absent \
      "$WO15_OVERLAY_GUARD" "$WO15_OVERLAY_RECEIPT" "$WO15_TERMINAL_FAILURE" \
      "${WO15_GUARD_DIR}/dma-single-consumed.json" \
      "${WO15_GUARD_DIR}/dma-three-consumed.json" \
      "${WO15_GUARD_DIR}/dma-single-pass.json" \
      "${WO16_SMOKE_GUARD}" "${WO16_GUARD_DIR}/stageb-batch-consumed.json"
    ;;
  overlay)
    require_absent "$WO15_OVERLAY_GUARD" "$WO15_OVERLAY_RECEIPT" "$WO15_TERMINAL_FAILURE"
    ;;
  smoke3)
    require_present "$WO15_OVERLAY_GUARD" "$WO15_OVERLAY_RECEIPT"
    require_absent "$WO15_TERMINAL_FAILURE" "$WO16_SMOKE_GUARD"
    ;;
esac

CMA_RECOVERY_ATTEMPTED=0
CMA_RECOVERY_BEFORE_KIB="${CMA_FREE_KIB:-}"
CMA_RECOVERY_AFTER_KIB="${CMA_FREE_KIB:-}"
if [[ -z "$CMA_FREE_KIB" || "$CMA_FREE_KIB" -lt 32768 ]]; then
  if [[ "$ACTION" != "smoke3" ]]; then
    echo "CmaFree is below 32768 KiB: ${CMA_FREE_KIB:-unavailable}" >&2
    exit 71
  fi
  CMA_RECOVERY_BEFORE_KIB="${CMA_FREE_KIB:-unavailable}"
  if ! "$TIMEOUT" --signal=TERM --kill-after=1s 12s "$PYTHON" \
    "$PACKAGE_DIR/pynq_device_probe.py" --recover-cma "$EXPECTED_BOOT_ID"; then
    echo "single guarded CMA recovery did not pass; Stage B guard remains untouched" >&2
    exit 71
  fi
  CMA_RECOVERY_ATTEMPTED=1
  LIVE_BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
  if [[ "$LIVE_BOOT_ID" != "$EXPECTED_BOOT_ID" ]]; then
    echo "boot_id changed during CMA recovery" >&2
    exit 70
  fi
  CMA_FREE_KIB="$(awk '$1 == "CmaFree:" { print $2; exit }' /proc/meminfo)"
  CMA_RECOVERY_AFTER_KIB="${CMA_FREE_KIB:-unavailable}"
  if [[ -z "$CMA_FREE_KIB" || "$CMA_FREE_KIB" -lt 32768 ]]; then
    echo "CmaFree remains below 32768 KiB after the single cache-drop attempt: ${CMA_FREE_KIB:-unavailable}" >&2
    exit 71
  fi
fi
export WO0016_CMA_RECOVERY_ATTEMPTED="$CMA_RECOVERY_ATTEMPTED"
export WO0016_CMA_RECOVERY_BEFORE_KIB="$CMA_RECOVERY_BEFORE_KIB"
export WO0016_CMA_RECOVERY_AFTER_KIB="$CMA_RECOVERY_AFTER_KIB"
echo "CmaFree_ready=${CMA_FREE_KIB} KiB cache_drop_attempted=${CMA_RECOVERY_ATTEMPTED}"

"$TIMEOUT" --signal=TERM --kill-after=1s 12s "$PYTHON" \
  "$PACKAGE_DIR/pynq_device_probe.py" "$EXPECTED_BOOT_ID"
if [[ "$ACTION" == "precheck" ]]; then
  exit 0
fi

if [[ "$ACTION" == "overlay" ]]; then
  WO15_SHA256="$1"
  EVIDENCE_JSON="$2"
  [[ "$EVIDENCE_JSON" == /* ]] || { echo "evidence path must be absolute" >&2; exit 64; }
  mkdir -p -- "$(dirname -- "$EVIDENCE_JSON")"
  exec "$PYTHON" "$PACKAGE_DIR/wo0015/board_once.py" \
    --mode overlay-only \
    --manifest "$PACKAGE_DIR/wo0015/wo0015-manifest.json" \
    --manifest-sha256 "$WO15_SHA256" \
    --execute --authorize-stage overlay-only --approve-once \
    --evidence-json "$EVIDENCE_JSON"
fi

WO15_SHA256="$1"
STAGEB_SHA256="$2"
ANALYSIS_JSON="$3"
EVIDENCE_JSON="$4"
[[ "$ANALYSIS_JSON" == /* && "$EVIDENCE_JSON" == /* ]] || {
  echo "analysis and evidence paths must be absolute" >&2
  exit 64
}
mkdir -p -- "$(dirname -- "$ANALYSIS_JSON")" "$(dirname -- "$EVIDENCE_JSON")"
exec "$PYTHON" "$PACKAGE_DIR/run_stream_counter.py" \
  --mode board --run-profile smoke3 --max-frames 3 --max-runtime-s 30 \
  --timeout-s 5 --camera /dev/video0 \
  --manifest "$PACKAGE_DIR/wo0015/wo0015-manifest.json" \
  --manifest-sha256 "$WO15_SHA256" \
  --stageb-manifest "$PACKAGE_DIR/stageb-runtime-manifest.json" \
  --stageb-manifest-sha256 "$STAGEB_SHA256" \
  --execute --authorize-stage wo0016-stream --approve-once \
  --analysis-json "$ANALYSIS_JSON" --evidence-json "$EVIDENCE_JSON" \
  --measure-preview-client
