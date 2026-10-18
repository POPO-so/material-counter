"""CMA free-page telemetry is global; allocator addresses gate DMA access."""
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import board_once as run
import board_preflight as pre
from test_board_preflight import make_manifest

class Telemetry(run.MockBoardAPI):
    def __init__(self,plan,samples):
        super().__init__(plan,initial_idle=False)
        self.samples=iter(samples)
    def cma_free_kib(self):return next(self.samples)

def plan_at(tmp_path):
    manifest,_=make_manifest(tmp_path,frame_count=3)
    return pre.validate_manifest(manifest)

@pytest.mark.parametrize('samples',[[37884,38612,38612,38612],
                                  [40000,40000,40000,39000],
                                  [16384,16384,16384,16384]])
def test_global_cma_rise_flat_or_nonrestoration_are_telemetry_with_valid_physical_addresses(tmp_path,samples):
    api=Telemetry(plan_at(tmp_path),samples)
    result=run.run_frames(api.plan,api,timeout_s=2,frame_limit=1)
    assert result['status']=='DMA_ONCE_PASS' and api.free_count==2
    assert [r['cma_free_kib'] for r in result['cma_allocation_samples']]==samples
    assert result['allocation_accounting']['hard_gate'] is False
    assert result['post_alloc_physical_gate']['status']=='POST_ALLOC_PHYSICAL_GATE_PASS'

@pytest.mark.parametrize('samples,allocated',[
    ([16383,40000],0),([40000,16383,40000],1),([40000,40000,16383,40000],2)])
def test_low_watermark_stops_before_any_register_write_and_releases_partial_buffers(tmp_path,samples,allocated):
    api=Telemetry(plan_at(tmp_path),samples)
    with pytest.raises(run.ExecutionFailure,match='watermark') as failure:
        run.run_frames(api.plan,api,timeout_s=2,frame_limit=1)
    assert api.write_trace==[] and len(api.allocations)==allocated and api.free_count==allocated
    assert not failure.value.transfer_started

def test_low_release_watermark_fails_after_completed_transfer_and_free(tmp_path):
    api=Telemetry(plan_at(tmp_path),[40000,39000,39000,16383])
    with pytest.raises(run.ExecutionFailure,match='watermark') as failure:
        run.run_frames(api.plan,api,timeout_s=2,frame_limit=1)
    assert api.free_count==2 and len(failure.value.evidence['frames'])==1
    assert not failure.value.dma_may_be_active

@pytest.mark.parametrize('physical',[None,0,False,1.5,'402653184'])
def test_physical_address_must_be_nonzero_integer_without_coercion(physical):
    buffer=SimpleNamespace(physical_address=physical,device_address=physical)
    with pytest.raises(pre.PreflightError,match='physical'):
        object.__new__(run.PynqBoardAPI).device_address(buffer)

@pytest.mark.parametrize('kind',['zero','overlap','misaligned','ddr_overflow','wrong_size'])
def test_watermark_pass_never_overrides_bad_physical_ranges(tmp_path,kind):
    class Invalid(Telemetry):
        def device_address(self,buf):
            if buf is self._s2mm_buffer:
                return {'zero':0,'overlap':self._mm2s_buffer.device_address,
                        'misaligned':0x18000001,'ddr_overflow':0x1ffff000}.get(kind,super().device_address(buf))
            return super().device_address(buf)
        def allocate(self,size):
            b=super().allocate(size)
            if kind=='wrong_size' and self._run_allocation_index==2:b.pop()
            return b
    api=Invalid(plan_at(tmp_path),[40000,41000,42000,43000])
    with pytest.raises(run.ExecutionFailure):run.run_frames(api.plan,api,timeout_s=2,frame_limit=1)
    assert api.write_trace==[] and api.free_count==2
