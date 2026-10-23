"""Copy only missing existing canonical PNG fixtures; never regenerate or overwrite."""
import hashlib,json,shutil
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
source=Path(r'C:\fpga\material-counter\data\synthetic\h3_full')
target=ROOT/'data/synthetic/h3_full'
pngs=sorted(source.glob('*/frames/frame_*.png'))
assert len(pngs)==487
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
copied=[]
hashes={}
for p in pngs:
    rel=p.relative_to(source)
    dest=target/rel
    digest=sha(p)
    if dest.exists():assert sha(dest)==digest
    else:
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,dest)
        copied.append(str(rel))
    assert sha(dest)==digest and sha(p)==digest
    hashes[str(rel)]=digest
assert len(list(target.glob('*/frames/frame_*.png')))==487
(OUT/'offline-fixture-restore.json').write_text(json.dumps({'source':str(source),'target':str(target),'copied_count':len(copied),'verified_count':len(hashes),'existing_files_overwritten':0,'regenerated':False,'sha256':hashes},indent=2),encoding='utf-8')
print('CANONICAL_FIXTURES_VERIFIED',len(hashes),'COPIED',len(copied))
