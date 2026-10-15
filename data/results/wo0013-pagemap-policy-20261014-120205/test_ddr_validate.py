import unittest
from ddr_validate import validate_buffer
class DDRValidation(unittest.TestCase):
    def test_present_zero_with_visible_pfns_rejected(self):
        from ddr_validate import validate_pagemap_rows
        with self.assertRaises(ValueError):validate_pagemap_rows([dict(present=True,swapped=False,pfn=0)],[0x15860000],True)
    def test_present_zero_with_masked_pfns_auxiliary(self):
        from ddr_validate import validate_pagemap_rows
        self.assertIsNone(validate_pagemap_rows([dict(present=True,swapped=False,pfn=0)],[0x15860000],False))
    def test_absent_drm_pte_auxiliary(self):
        from ddr_validate import validate_pagemap_rows
        self.assertIsNone(validate_pagemap_rows([dict(present=False,swapped=False,pfn=0)],[0x15860000],True))
    def test_pfn_absent_with_supported_allocator(self):
        evidence=self.evidence();evidence.update(physical_pages=None,supported_allocator=True)
        self.accepts(0x15860000,76816,**evidence)
    def test_pfn_absent_without_allocator_proof_rejected(self):
        evidence=self.evidence();evidence.update(physical_pages=None,supported_allocator=False)
        with self.assertRaises(ValueError):validate_buffer(0x15860000,76816,**evidence)
    def test_bad_pfn_never_overridden_by_allocator(self):
        evidence=self.evidence();evidence.update(physical_pages=[0x15861000]*19,supported_allocator=True)
        with self.assertRaises(ValueError):validate_buffer(0x15860000,76816,**evidence)
    def accepts(self,*args,**kwargs):
        try:validate_buffer(*args,**kwargs)
        except ValueError as error:self.fail('Valid proven buffer rejected: '+str(error))
    def evidence(self,address=0x15860000,length=76816):
        return dict(bo_address=address,bo_size=77824,physical_pages=[(address//4096+i)*4096 for i in range((length+4095)//4096)],virtual_offset=0,contiguous=True,ram_ranges=[(0,0x20000000)],dt_ranges=[(0,0x20000000)],axi_ranges=[(0,0x20000000)],address_bits=32,alignment=8)
    def test_valid_cma_above_metadata_size(self):
        self.accepts(0x15860000,76816,**self.evidence())
    def test_illegal_ranges_and_allocation_evidence(self):
        cases=[('negative',-8,76816,{}),('zero',0x15860000,0,{}),('dma_overflow',0xfffffff8,16,{}),('axi_end',0x20000000,8,{}),('straddles_axi',0x1ffff000,8192,{}),('unaligned',0x15860001,76816,{}),('bo_mismatch',0x15860000,76816,{'bo_address':0x15861000}),('bo_too_small',0x15860000,76816,{'bo_size':4096}),('not_contiguous',0x15860000,76816,{'contiguous':False}),('missing_pages',0x15860000,76816,{'physical_pages':[]}),('page_gap',0x15860000,76816,{'physical_pages':[0x15860000,0x15862000]}),('RAM_hole',0x15860000,76816,{'ram_ranges':[(0,0x15861000),(0x15862000,0x20000000)]}),('DT_disagreement',0x15860000,76816,{'dt_ranges':[(0,0x10000000)]}),('empty_AXI',0x15860000,76816,{'axi_ranges':[]}),('invalid_window',0x15860000,76816,{'axi_ranges':[(0x20000000,0)]}),('bad_width',0x15860000,76816,{'address_bits':28})]
        for name,address,length,changed in cases:
            with self.subTest(name=name):
                evidence=self.evidence();evidence.update(changed)
                with self.assertRaises(ValueError):validate_buffer(address,length,**evidence)
    def test_last_valid_aligned_transfer(self):
        a=0x1ffff000;e=self.evidence(a,4096);e['bo_size']=4096
        self.accepts(a,4096,**e)
    def test_nonzero_window_base(self):
        e=self.evidence();e['axi_ranges']=[(0x10000000,0x20000000)]
        self.accepts(0x15860000,76816,**e)
        e['axi_ranges']=[(0x16000000,0x20000000)]
        with self.assertRaises(ValueError):validate_buffer(0x15860000,76816,**e)
if __name__=='__main__':unittest.main(verbosity=2)
