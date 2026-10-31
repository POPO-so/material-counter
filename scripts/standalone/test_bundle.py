import hashlib
import json
from pathlib import Path
import pytest
try:
    from scripts.standalone.bundle import (verify_bundle, verify_offload_capabilities,
                                           required_addresses)
except ModuleNotFoundError:
    verify_bundle = None
    verify_offload_capabilities = None
    required_addresses = {}

OFFLOAD = {
    "abi": 1,
    "preview": {"signature": "PBO1", "base": 0x43C50000,
                "source_geometry": [640, 400], "viewport": [384, 146, 512, 320]},
    "input": {"signature": "RAW1", "mode": "raw"},
    "buttons": {"interrupt": True, "intc_index": 3},
}

def bundle(tmp, *, offload=False):
    if verify_bundle is None:
        pytest.fail("display bundle verification is missing")
    (tmp/"display.bit").write_bytes(b"test bit")
    ranges = "".join(f'<MEMRANGE INSTANCE="{name}" BASEVALUE="{hex(address)}"/>' for name,address in required_addresses.items())
    if offload:
        ranges += '<MEMRANGE INSTANCE="binary_overlay" BASEVALUE="0x43c50000"/><MEMRANGE INSTANCE="image_pipeline" BASEVALUE="0x43c20000"/>'
    params = "".join(f'<PARAMETER NAME="{name}" VALUE="{value}"/>' for name,value in {"C_NUM_FSTORES":4,"C_INCLUDE_S2MM":0,"C_M_AXIS_MM2S_TDATA_WIDTH":32,"C_M_AXI_MM2S_DATA_WIDTH":32}.items())
    gpio = '<MODULE INSTANCE="btns_gpio"><PARAMETERS><PARAMETER NAME="C_INTERRUPT_PRESENT" VALUE="1"/></PARAMETERS><PORT NAME="ip2intc_irpt"><CONNECTIONS><CONNECTION INSTANCE="video_interrupts" PORT="In3"/></CONNECTIONS></PORT></MODULE>' if offload else ''
    intc = '<MODULE INSTANCE="video_interrupts"><PARAMETERS><PARAMETER NAME="NUM_PORTS" VALUE="4"/></PARAMETERS></MODULE>' if offload else ''
    (tmp/"display.hwh").write_text('<ROOT>'+ranges+'<MODULE INSTANCE="axi_vdma"><PARAMETERS>'+params+'</PARAMETERS></MODULE>'+gpio+intc+'</ROOT>')
    data = {"schema":"material-counter.display-build.v1","ready_for_board":True,"files":{k:{"path":"display."+k,"sha256":hashlib.sha256((tmp/("display."+k)).read_bytes()).hexdigest()} for k in ["bit","hwh"]}}
    if offload:
        data["offload"] = OFFLOAD
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

def test_offload_bundle_checks_declared_capabilities_and_hwh_wiring(tmp_path):
    p,digest=bundle(tmp_path,offload=True)
    assert verify_bundle(p,digest)["hwh"] == tmp_path/"display.hwh"
    assert verify_offload_capabilities(json.loads(p.read_text()),tmp_path/"display.hwh")["register_signatures"] == "runtime_only"

@pytest.mark.parametrize("mutate", [
    lambda data: data["offload"]["preview"].update(base=0x43C40000),
    lambda data: data["offload"]["preview"].update(viewport=[0, 0, 640, 400]),
    lambda data: data["offload"]["input"].update(mode="packet"),
    lambda data: data["offload"]["buttons"].update(intc_index=2),
    lambda data: data["offload"]["buttons"].update(interrupt=1),
])
def test_offload_manifest_must_match_fixed_contract(tmp_path, mutate):
    p,_=bundle(tmp_path,offload=True); data=json.loads(p.read_text()); mutate(data)
    with pytest.raises(ValueError,match="offload"):
        verify_offload_capabilities(data,tmp_path/"display.hwh")

@pytest.mark.parametrize("old,new", [
    ("0x43c50000", "0x43c60000"), ("0x43c20000", "0x43c30000"),
    ("C_INTERRUPT_PRESENT\" VALUE=\"1", "C_INTERRUPT_PRESENT\" VALUE=\"0"),
    ("NUM_PORTS\" VALUE=\"4", "NUM_PORTS\" VALUE=\"3"),
    ("PORT=\"In3", "PORT=\"In2"),
])
def test_offload_hwh_must_prove_irq_and_register_map(tmp_path, old, new):
    p,_=bundle(tmp_path,offload=True); hwh=tmp_path/"display.hwh"
    hwh.write_text(hwh.read_text().replace(old,new))
    with pytest.raises(ValueError):
        verify_offload_capabilities(json.loads(p.read_text()),hwh)

def test_legacy_bundle_without_offload_remains_verifiable(tmp_path):
    p,digest=bundle(tmp_path)
    assert verify_bundle(p,digest)["bit"] == tmp_path/"display.bit"
