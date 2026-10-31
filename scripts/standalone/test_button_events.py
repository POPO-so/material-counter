import asyncio

try:
    from scripts.standalone.button_events import ButtonEventRunner, interrupt_available
except ModuleNotFoundError:
    ButtonEventRunner = interrupt_available = None
from scripts.standalone.controls import ButtonDecoder


class Edge:
    interrupt_present = True

    def __init__(self):
        self.waiters = []
        self.bits = 0
        self.wait_calls = 0

    async def wait_for_interrupt_async(self):
        self.wait_calls += 1
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        future.add_done_callback(lambda done: self.waiters.remove(done)
                                 if done in self.waiters else None)
        await future

    def read_bits(self):
        return self.bits

    def fire(self, bits):
        self.bits = bits
        for waiter in self.waiters:
            if not waiter.done():
                waiter.set_result(None)
        self.waiters.clear()


class ManualTimer:
    def __init__(self):
        self.now = 0.0
        self.waiters = []

    async def sleep(self, delay):
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.now + delay, future))
        await future

    def advance(self, amount):
        self.now += amount
        for deadline, future in self.waiters:
            if deadline <= self.now and not future.done():
                future.set_result(None)


def test_event_runner_uses_deadline_wait_and_stops_on_cancel():
    assert ButtonEventRunner is not None, "button event runner is not implemented"
    async def scenario():
        source = Edge()
        timer = ManualTimer()
        events = []
        runner = ButtonEventRunner(source,
                                   source.read_bits, events.extend,
                                   decoder=ButtonDecoder(),
                                   clock=lambda: timer.now, sleep=timer.sleep)
        task = asyncio.create_task(runner.run())
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(source.waiters) == 1
        source.fire(1)
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(source.waiters) == 1
        timer.advance(.03)
        for _ in range(5):
            await asyncio.sleep(0)
        assert events == ["clear"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert source.waiters == []

    asyncio.run(scenario())


def test_missing_gpio_interrupt_is_reported_without_polling():
    class NoInterrupt:
        interrupt_present = False

    async def scenario():
        runner = ButtonEventRunner(NoInterrupt(), lambda: 0, lambda _: None)
        try:
            await runner.run()
        except RuntimeError as exc:
            assert "interrupt unavailable" in str(exc)
        else:
            raise AssertionError("missing IRQ was not reported")

    asyncio.run(scenario())


def test_interrupt_capability_probe_requires_async_wait_method():
    assert interrupt_available is not None
    assert interrupt_available(Edge())
    assert not interrupt_available(object())


def test_initial_gpio_state_is_sampled_and_irq_wait_survives_timer_deadline():
    async def scenario():
        source = Edge()
        source.bits = 1
        timer = ManualTimer()
        events = []
        reads = []

        def read_bits():
            reads.append(source.read_bits())
            return reads[-1]

        commands_seen = asyncio.Event()

        def on_commands(commands):
            events.extend(commands)
            commands_seen.set()

        runner = ButtonEventRunner(source, read_bits, on_commands,
                                   clock=lambda: timer.now, sleep=timer.sleep)
        task = asyncio.create_task(runner.run())
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(source.waiters) == 1
        timer.advance(.03)
        await commands_seen.wait()
        assert events == ["clear"]
        assert source.wait_calls == 1
        assert len(source.waiters) == 1
        assert reads == [1], "timer deadline must not read GPIO"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_irq_wait_failure_is_propagated_once_without_retry_loop():
    class BrokenInterrupt:
        interrupt_present = True

        def __init__(self):
            self.calls = 0

        async def wait_for_interrupt_async(self):
            self.calls += 1
            raise OSError("interrupt fd failed")

    async def scenario():
        interrupt = BrokenInterrupt()
        runner = ButtonEventRunner(interrupt, lambda: 0, lambda _: None)
        try:
            await asyncio.wait_for(runner.run(), timeout=.1)
        except OSError as exc:
            assert "interrupt fd failed" in str(exc)
        else:
            raise AssertionError("IRQ waiter error was swallowed")
        assert interrupt.calls == 1

    asyncio.run(scenario())
