"""Prepare the final Two-Tier package and individual manually gated board requests."""
import ast,base64,json,shlex
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
old=(OUT.parent/'wo0015-cma-compat-20261015/package_updated.py').read_text(encoding='utf-8')
old=old.replace("'wo0015-retry3-cma-bundle-20261015'","'wo0015-retry3-two-tier-bundle-20261015'")
old=old.replace('OFFLINE_PACKAGE_PASS_RUNTIME_ADMISSION_STILL_BLOCKED','OFFLINE_PACKAGE_PASS_AUTHORIZED_RETRY3')
old=old.replace("'runtime_admission_approved':False", "'runtime_admission_approved':True")
old=old.replace('Current captured boot has neither exact CMA range metadata nor cma= evidence; no hardware execution is authorized.',
                'Captain authorizes MEMINFO_IOMEM_VERIFIED and one ordered Retry3 sequence; fresh board admission and post-allocation gates remain mandatory.')
ast.parse(old)
(OUT/'package_updated.py').write_text(old,encoding='utf-8')
print('PACKAGE_DRIVER_PREPARED')
