from pathlib import Path
import camera_only_preview as preview
preview.EXPECTED_BOOT = '442a249b-1a20-41a1-bbc6-46debb0f4af8'
preview.WO_ROOT = Path('/home/xilinx/web-preview-20261027-124938/calibration')
raise SystemExit(preview.main(['--expected-boot-id', preview.EXPECTED_BOOT, '--output-dir', str(preview.WO_ROOT / ('camera-preview-' + preview.EXPECTED_BOOT + '-06')), '--calibration-mode']))
