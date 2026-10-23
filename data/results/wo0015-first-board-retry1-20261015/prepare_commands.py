"""Produce individual requests. This file never dispatches a hardware stage."""
import base64,json,shlex
from pathlib import Path
OUT=Path(__file__).resolve().parent
WO='/home/xilinx/wo0015_first_board_retry1_20261015'
PY='/usr/local/share/pynq-venv/bin/python'
H=json.loads((OUT/'authorization-rebind.json').read_text())['manifest_sha256']
base=f'sudo -S -p "" env XILINX_XRT=/usr {PY} -u {WO}/board_once.py'
templates={}
for mode in ['offline','overlay-only','dma-single','dma-three']:
    command=base+f' --mode {mode} --manifest {WO}/run-manifest.json --manifest-sha256 {H}'
    if mode!='offline':command+=f' --execute --authorize-stage {mode} --approve-once --timeout-s 5'
    command+=f' --evidence-json {WO}/{mode}-result.json'
    templates[mode]={'op':'command','command':command,'timeout':60}
for name,file in [('initial-state','read_initial_state.py'),('exit-os','read_exit_os.py')]:
    encoded=base64.b64encode((OUT/file).read_bytes()).decode()
    source='import base64;exec(compile(base64.b64decode('+repr(encoded)+'),'+repr(file)+',"exec"))'
    templates[name]={'op':'command','command':f'sudo -S -p "" env XILINX_XRT=/usr {PY} -u -c '+shlex.quote(source),'timeout':45}
templates['upload']={'op':'upload','path':str(OUT.parent/'wo0015-first-board-bundle-20261015.zip'),'sha256':'873d245ad92926b00e9f57b2793220ab14dca0c287f49c213d7af5d36dd14879'}
templates['configure']={'op':'upload-config','files':{n:str(OUT/'authorized-deployment'/n) for n in ('board_preflight.py','run-manifest.json','SHA256SUMS.json')}}
(OUT/'command-templates.json').write_text(json.dumps(templates,indent=2),encoding='utf-8')
request=OUT.parents[2]/'docs/verification/wo-0015-first-board-request-20261015.md'
with request.open('a',encoding='utf-8') as f:
    f.write('\n本次实际三阶段命令（传输层通过原生隐藏输入提供 sudo 凭据；每条分别核验后执行）：\n\n```bash\n')
    for mode in ('overlay-only','dma-single','dma-three'):f.write(templates[mode]['command']+'\n')
    f.write('```\n\n补充初态只读核对：Checker/Packer 全寄存器与 DMA CR/SR/地址/长度，要求 Halted=0、Err=0；首次 Idle=0 可接受。此快照不写寄存器、不启动 DMA。RTL 无单独版本寄存器，以 WO14 Checker、WO15 Packer 签名记录设计身份。\n')
print('INDIVIDUAL_COMMAND_TEMPLATES_READY')
