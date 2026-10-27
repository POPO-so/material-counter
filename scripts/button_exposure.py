"""PYNQ-Z2 BTN0/BTN1 camera exposure adjustment; never loads an overlay."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import time


class ExposureButtons:
    def __init__(self, read, write, minimum, maximum):
        self.read, self.write = read, write
        self.minimum, self.maximum = minimum, maximum
        self.candidate = self.stable = 0
        self.changed_at = 0.0
        self.armed = True

    def poll(self, mask, now):
        mask &= 3
        if mask != self.candidate:
            self.candidate, self.changed_at = mask, now
        if now - self.changed_at < .05 or mask == self.stable:
            return None
        self.stable = mask
        if mask == 0:
            self.armed = True
            return None
        if not self.armed:
            return None
        self.armed = False
        if mask not in (1, 2):
            return None
        current = self.read()
        requested = max(self.minimum, min(self.maximum, current + (-1 if mask == 1 else 1)))
        if requested == current:
            return None
        self.write(requested)
        actual = self.read()
        if actual != requested:
            raise RuntimeError(f'Exposure readback mismatch: requested {requested}, actual {actual}')
        return actual


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='/dev/video0')
    parser.add_argument('--base-bitfile', type=Path, required=True)
    parser.add_argument('--state-file', type=Path, required=True)
    args = parser.parse_args()
    bitfile = args.base_bitfile.resolve(strict=True)
    state = json.loads(args.state_file.read_text())
    if Path(state['bitfile_name']).resolve() != bitfile:
        raise RuntimeError('Base overlay is not the recorded current design; no GPIO access attempted')
    if hashlib.sha1(bitfile.read_bytes()).hexdigest() != state['bitfile_hash']:
        raise RuntimeError('Current overlay hash does not match the base bitfile')
    lock = open('/run/lock/button-exposure.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def command(*options):
        return subprocess.check_output(['v4l2-ctl', '-d', args.device, *options],
                                       text=True, stderr=subprocess.STDOUT, timeout=3)

    def read():
        output = command('--get-ctrl=auto_exposure,exposure_time_absolute')
        if not re.search(r'auto_exposure:\s*1\b', output):
            raise RuntimeError('Manual exposure is required')
        return int(re.search(r'exposure_time_absolute:\s*(\d+)', output).group(1))

    output = command('--list-ctrls')
    bounds = re.search(r'exposure_time_absolute[^\n]*min=(\d+) max=(\d+) step=(\d+)', output)
    if not bounds or int(bounds[3]) != 1:
        raise RuntimeError('Camera does not expose a one-unit (0.1 ms) exposure step')
    print(f'Initial exposure={read()} units; BTN0=-0.1ms BTN1=+0.1ms; release between presses', flush=True)
    # Read current metadata only. Loading or replacing the FPGA design is external.
    from pynq import Overlay
    overlay = Overlay(str(bitfile), download=False)
    buttons = overlay.btns_gpio.channel1
    buttons.setlength(4)
    buttons.setdirection('in')
    controller = ExposureButtons(read, lambda value: command(f'--set-ctrl=exposure_time_absolute={value}'),
                                 int(bounds[1]), int(bounds[2]))
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print('READY: GPIO connected; release BTN0/BTN1 before first adjustment', flush=True)
    released = False
    while not stopping:
        mask = buttons.read() & 3
        if not released:
            released = mask == 0
        else:
            actual = controller.poll(mask, time.monotonic())
            if actual is not None:
                print(f'Exposure={actual} units = {actual / 10:.1f} ms', flush=True)
        time.sleep(.01)
    print('Stopped; camera settings retained', flush=True)


if __name__ == '__main__':
    main()
