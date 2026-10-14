"""Remove native terminal pre-auth output; keep execution evidence byte-for-byte."""
import json
from pathlib import Path
OUT=Path(__file__).resolve().parent
record_path=OUT/'authentication-output-redactions.json'
records=json.loads(record_path.read_text()) if record_path.exists() else []
done={r['file'] for r in records}
active=json.loads((OUT/'status.json').read_text()).get('stage','')
for path in OUT.glob('*-stdout.bin'):
    if path.name in done or path.name.startswith(active.removesuffix('_AUTH_BEGIN')):
        continue
    data=path.read_bytes()
    locations=[data.find(marker) for marker in (b'ACTION_BEGIN',b'VERIFICATION_STARTED')]
    locations=[i for i in locations if i>=0]
    if not locations:continue
    cut=min(locations)
    if cut:
        prefix=b'[Native authentication terminal prefix redacted; execution evidence follows]\r\n'
        path.write_bytes(prefix+data[cut:])
        records.append({'file':path.name,'removed_pre_execution_bytes':cut,
                        'execution_evidence_preserved':True})
record_path.write_text(json.dumps(records,indent=2)+'\n')
print('Native authentication prefixes redacted: '+str(len(records)))
