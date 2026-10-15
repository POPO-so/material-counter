exec(compile(open('/home/xilinx/wo0013_level2_once_20261014_113914/remote-common.py').read(),'remote-common','exec'))
handle=lock();guard();verified_loaded()
import subprocess
commands=[]
for args in [['uname','-a'],['id'],['uptime'],['free','-m'],['dmesg','--level=err,warn']]:
    result=subprocess.run(args,capture_output=True,text=True,timeout=5)
    commands.append({'args':args,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
    assert result.returncode==0
event('POST_HEALTH_PASS',boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),fpga_state=Path('/sys/class/fpga_manager/fpga0/state').read_text().strip(),commands=commands)
