"""Transport preparation only; new deployment extracts as its ordinary owner."""
import ast,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
source=(OUT.parent/'wo0015-first-board-retry2-20261015/controller.py').read_text(encoding='utf-8')
source=source.replace("REMOTE = '/home/xilinx/wo0015_first_board_retry1_20261015'",
                      "REMOTE = '/home/xilinx/wo0015_first_board_retry3_20261015'")
# Upload owns the new directory as xilinx; extracting without sudo keeps
# all package/config files writable by that same identity.
needle="rc, _ = execute(client, sudo_password, sudo_python(source), name)"
replacement="rc, _ = execute(client, sudo_password, sudo_python(source).replace('sudo -S -p \\\"\\\" ', '', 1), name)"
replacement="rc, _ = execute(client, sudo_password, sudo_python(source)[len('sudo -S -p \"\" '):], name)"
assert source.count(needle)==1
source=source.replace(needle,replacement)
ast.parse(source)
(OUT/'controller.py').write_text(source,encoding='utf-8')
for label,path in [('main',Path(r'C:\fpga\material-counter')),('worktree',ROOT)]:
    (OUT/(label+'-git-before.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
print('CONTROLLER_READY_NO_BOARD_COMMAND_DISPATCH')
