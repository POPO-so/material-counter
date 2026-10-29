import pytest

from scripts.standalone.g1_run import _probe_display_buffers


class Buffer:
    def __init__(self):
        self.freed = False

    def freebuffer(self):
        self.freed = True


class Owner:
    def __init__(self):
        self.resources = []

    def retain(self, resource):
        self.resources.append(resource)
        return resource


def test_display_admission_probe_allocates_and_releases_four_gray8_slots():
    owner = Owner()
    buffers = []
    requests = []

    def allocate(*, shape, dtype):
        requests.append((shape, dtype))
        buffer = Buffer()
        buffers.append(buffer)
        return buffer

    _probe_display_buffers(owner, allocate)

    assert requests == [((720, 1280), "u1")] * 4
    assert owner.resources == buffers
    assert all(buffer.freed for buffer in buffers)


def test_display_admission_probe_releases_allocations_when_a_later_slot_fails():
    owner = Owner()
    buffers = []

    def allocate(*, shape, dtype):
        assert (shape, dtype) == ((720, 1280), "u1")
        if len(buffers) == 2:
            raise RuntimeError("BO allocation failed")
        buffer = Buffer()
        buffers.append(buffer)
        return buffer

    with pytest.raises(RuntimeError, match="BO allocation failed"):
        _probe_display_buffers(owner, allocate)

    assert owner.resources == buffers
    assert len(buffers) == 2
    assert all(buffer.freed for buffer in buffers)
