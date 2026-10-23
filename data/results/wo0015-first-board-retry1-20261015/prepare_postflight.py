import ast
from pathlib import Path
OUT=Path(__file__).resolve().parent
old=(OUT.parent/'wo0015-first-board-20261015/postflight.py').read_text(encoding='utf-8')
new=old.replace('import os,json,subprocess','import os,json,subprocess,hashlib').replace('/home/xilinx/wo0015_first_board_20261015','/home/xilinx/wo0015_first_board_retry1_20261015')
needle="'remote_bundle_directory_exists':Path('/home/xilinx/wo0015_first_board_retry1_20261015').exists(),"
replacement=needle+"\n'deployed_files':{n:{'sha256':hashlib.sha256((Path('/home/xilinx/wo0015_first_board_retry1_20261015')/n).read_bytes()).hexdigest(),'uid':(Path('/home/xilinx/wo0015_first_board_retry1_20261015')/n).stat().st_uid,'mode':oct((Path('/home/xilinx/wo0015_first_board_retry1_20261015')/n).stat().st_mode & 0o777)} for n in ('wo15.bit','wo15.hwh','board_preflight.py','run-manifest.json','SHA256SUMS.json')},"
assert needle in new
new=new.replace(needle,replacement)
ast.parse(new)
(OUT/'postflight.py').write_text(new,encoding='utf-8')
