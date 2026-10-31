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
                 *, decoder=None, clock=time.monotonic):
        self.interrupt = interrupt
        self.read_bits = read_bits
        self.on_commands = on_commands
        self.decoder = decoder or ButtonDecoder()
        self.clock = clock

    async def run(self):
        wait = getattr(self.interrupt, "wait_for_interrupt_async", None)
        if not interrupt_available(self.interrupt):
            raise RuntimeError("GPIO interrupt unavailable; button event runner cannot start")
        while True:
            edge_task = asyncio.create_task(wait())
            deadline = self.decoder.next_deadline
            timer_task = None
            tasks = {edge_task}
            if deadline is not None:
                timer_task = asyncio.create_task(asyncio.sleep(max(0, deadline - self.clock())))
                tasks.add(timer_task)
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if edge_task in done:
                    bits = self.read_bits()
                    commands = self.decoder.update(bits, self.clock())
                else:
                    commands = self.decoder.advance(self.clock())
                if commands:
                    self.on_commands(commands)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
