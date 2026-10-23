"""Native hidden credentials and strict host fingerprint for one metadata probe."""
import base64,getpass,hashlib,json,traceback
from pathlib import Path
import transport as t
OUT=Path(__file__).resolve().parent
def main():
    with (OUT/'probe-attempt.json').open('x') as f:json.dump({'read_only':True,'boot_id':t.BOOT},f)
    assert hashlib.sha256(t.KNOWN.read_bytes()).hexdigest()==t.KNOWN_SHA
    t.event('AWAITING_NATIVE_HIDDEN_CREDENTIALS_METADATA_ONLY')
    password=getpass.getpass('CMA READ-ONLY PROBE SSH password (hidden): ')
    sudo_password=getpass.getpass('CMA READ-ONLY PROBE sudo password (hidden): ')
    c=t.paramiko.SSHClient();c.load_host_keys(str(t.KNOWN));c.set_missing_host_key_policy(t.paramiko.RejectPolicy())
    c.connect('192.168.2.99',username='xilinx',password=password,allow_agent=False,look_for_keys=False,timeout=5,banner_timeout=10,auth_timeout=15,disabled_algorithms={'keys':['ssh-rsa','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521']})
    del password
    key=c.get_transport().get_remote_server_key()
    fp='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
    assert fp==t.FINGERPRINT
    t.event('STRICT_SSH_AUTHENTICATED',fingerprint=fp)
    rc,data=t.execute(c,sudo_password,t.sudo_python((OUT/'remote_probe.py').read_text()),'cma-readonly-probe',45)
    c.close();del sudo_password
    assert rc==0,'metadata probe failed; no retry'
    r=json.loads(data)
    (OUT/'board-cma-probe.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
    for key in ('cmdline','chosen_bootargs','dmesg_filtered','iomem','meminfo','page_size'):
        (OUT/(key+'.txt')).write_text(r[key]['stdout'],encoding='utf-8')
    (OUT/'probe-summary.json').write_text(json.dumps({'status':'READONLY_PROBE_PASS','boot_id':r['boot_id_after'],'host_fingerprint':fp,'known_hosts_unchanged':hashlib.sha256(t.KNOWN.read_bytes()).hexdigest()==t.KNOWN_SHA,'prohibited_operations':r['prohibited_operations']},indent=2),encoding='utf-8')
    t.event('READONLY_PROBE_COMPLETE_SSH_CLOSED')
if __name__=='__main__':
    try:main()
    except BaseException:
        (OUT/'probe-error.txt').write_text(traceback.format_exc(),encoding='utf-8');t.event('PROBE_FAIL_STOPPED_NO_RETRY')
    input('Read-only probe finished. Press Enter to close: ')
