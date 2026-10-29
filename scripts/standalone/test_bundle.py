import hashlib
import json
from pathlib import Path
import pytest
try:
    from scripts.standalone.bundle import verify_bundle, required_addresses
except ModuleNotFoundError:
    verify_bundle = None
    required_addresses = {}

def bundle(tmp):
    if verify_bundle is None:
        pytest.fail("display bundle verification is missing")
    (tmp/"display.bit").write_bytes(b"test bit")
    ranges = "".join(f'<MEMRANGE INSTANCE="{name}" BASEVALUE="{hex(address)}"/>' for name,address in required_addresses.items())
    params = "".join(f'<PARAMETER NAME="{name}" VALUE="{value}"/>' for name,value in {"C_NUM_FSTORES":4,"C_INCLUDE_S2MM":0,"C_M_AXIS_MM2S_TDATA_WIDTH":32,"C_M_AXI_MM2S_DATA_WIDTH":32}.items())
    (tmp/"display.hwh").write_text('<ROOT>'+ranges+'<MODULE INSTANCE="axi_vdma"><PARAMETERS>'+params+'</PARAMETERS></MODULE></ROOT>')
    data = {"schema":"material-counter.display-build.v1","ready_for_board":True,"files":{k:{"path":"display."+k,"sha256":hashlib.sha256((tmp/("display."+k)).read_bytes()).hexdigest()} for k in ["bit","hwh"]}}
    p=tmp/"manifest.json";p.write_text(json.dumps(data))
    return p,hashlib.sha256(p.read_bytes()).hexdigest()

def test_validated_bundle_rejects_modified_bit_before_any_hardware(tmp_path):
    p,digest=bundle(tmp_path)
    assert verify_bundle(p,digest)["bit"]==tmp_path/"display.bit"
    (tmp_path/"display.bit").write_bytes(b"changed")
    with pytest.raises(ValueError,match="hash"):
        verify_bundle(p,digest)

def test_separate_manifest_digest_is_required(tmp_path):
    p,digest=bundle(tmp_path)
    with pytest.raises(ValueError,match="manifest"):
        verify_bundle(p,"0"*64)

def test_unreviewed_build_and_path_escape_are_rejected(tmp_path):
    p,digest=bundle(tmp_path);r=json.loads(p.read_text());r["ready_for_board"]=False;p.write_text(json.dumps(r))
    with pytest.raises(ValueError,match="review"):
        verify_bundle(p,hashlib.sha256(p.read_bytes()).hexdigest())
    r["ready_for_board"]=True;r["files"]["bit"]["path"]="../escape.bit";p.write_text(json.dumps(r))
    with pytest.raises(ValueError,match="path"):
        verify_bundle(p,hashlib.sha256(p.read_bytes()).hexdigest())

def test_mismatched_hwh_address_is_rejected(tmp_path):
    p,digest=bundle(tmp_path);q=tmp_path/"display.hwh";q.write_text(q.read_text().replace("0x43000000","0x44000000"))
    r=json.loads(p.read_text());r["files"]["hwh"]["sha256"]=hashlib.sha256(q.read_bytes()).hexdigest();p.write_text(json.dumps(r))
    with pytest.raises(ValueError,match="address"):
        verify_bundle(p,hashlib.sha256(p.read_bytes()).hexdigest())
