"""Validate a proven contiguous BO against runtime RAM and exported AXI windows.

All intervals use exclusive ends. XRT topology capacity is deliberately not an
absolute physical-address limit; physical pages and bo.address establish identity.
"""
def validate_buffer(address,length,*,bo_address,bo_size,physical_pages,virtual_offset,
                    contiguous,ram_ranges,dt_ranges,axi_ranges,address_bits=32,alignment=8,supported_allocator=False):
    values=[address,length,bo_address,bo_size,virtual_offset,address_bits,alignment]
    if any(type(x) is not int for x in values):raise ValueError('Integer evidence required')
    if length<=0 or address<0 or bo_size<length:raise ValueError('Invalid buffer extent')
    if not 1<=address_bits<=64 or alignment<=0:raise ValueError('Invalid DMA properties')
    end=address+length
    if end>1<<address_bits:raise ValueError('DMA address width exceeded')
    if address%alignment:raise ValueError('DMA alignment')
    if not contiguous or bo_address!=address:raise ValueError('BO identity/contiguity')
    if not 0<=virtual_offset<4096 or virtual_offset!=address%4096:raise ValueError('Page offset mismatch')
    pages=(virtual_offset+length+4095)//4096
    expected=[address-virtual_offset+i*4096 for i in range(pages)]
    if physical_pages is None:
        if supported_allocator is not True:raise ValueError('Missing allocator/physical page proof')
    elif list(physical_pages)!=expected:raise ValueError('Discontinuous or mismatched physical pages')
    for name,ranges in [('Linux RAM',ram_ranges),('device-tree RAM',dt_ranges),('AXI window',axi_ranges)]:
        if not ranges:raise ValueError('Missing '+name)
        if any(type(a) is not int or type(b) is not int or a<0 or b<=a for a,b in ranges):raise ValueError('Invalid '+name)
        if not any(a<=address and end<=b for a,b in ranges):raise ValueError('Buffer not fully inside '+name)
    return {'start':address,'end_exclusive':end,'length':length,'physical_pages_checked':pages if physical_pages is not None else 0,'continuity_basis':'pagemap' if physical_pages is not None else 'supported_pynq_allocator_verified_runtime'}
