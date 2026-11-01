"""Async edge-driven button event loop with cancellable timer deadlines."""
import asyncio
import time

from .controls import ButtonDecoder


def interrupt_available(interrupt):
    """Return whether an interrupt object exposes the async wait API."""
    return (interrupt is not None and
            getattr(interrupt, "interrupt_present", True) is not False and
            callable(getattr(interrupt, "wait_for_interrupt_async", None)))


class ButtonEventRunner:
    def __init__(self, interrupt, read_bits, on_commands,
                 *, decoder=None, clock=time.monotonic, sleep=asyncio.sleep):
        self.interrupt = interrupt
        self.read_bits = read_bits
        self.on_commands = on_commands
        self.decoder = decoder or ButtonDecoder()
        self.clock = clock
        self.sleep = sleep

    async def run(self):
        wait = getattr(self.interrupt, "wait_for_interrupt_async", None)
        if not interrupt_available(self.interrupt):
            raise RuntimeError("GPIO interrupt unavailable; button event runner cannot start")
        initial = self.decoder.update(self.read_bits(), self.clock())
        if initial:
            self.on_commands(initial)
        edge_task = asyncio.create_task(wait())
        timer_task = None
        timer_deadline = None
        try:
            while True:
                deadline = self.decoder.next_deadline
                if deadline != timer_deadline:
                    if timer_task is not None:
                        timer_task.cancel()
                        await asyncio.gather(timer_task, return_exceptions=True)
                        timer_task = None
                    timer_deadline = deadline
                    if deadline is not None:
                        timer_task = asyncio.create_task(
                            self.sleep(max(0, deadline - self.clock())))

                waiting = {edge_task}
                if timer_task is not None:
                    waiting.add(timer_task)
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                timer_fired = timer_task is not None and timer_task in done
                if timer_fired:
                    timer_task = None
                    timer_deadline = None

                # Read the result explicitly. IRQ failures must stop this owner
                # instead of being swallowed and retried in a hot loop.
                if edge_task in done:
                    edge_task.result()
                    commands = self.decoder.update(self.read_bits(), self.clock())
                    edge_task = asyncio.create_task(wait())
                elif timer_fired:
                    commands = self.decoder.advance(self.clock())
                else:
                    continue
                if commands:
                    self.on_commands(commands)
        finally:
            pending = [task for task in (edge_task, timer_task) if task is not None]
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
