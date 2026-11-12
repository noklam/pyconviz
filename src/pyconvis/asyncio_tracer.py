"""Real runtime execution tracer for Python asyncio programs."""

from __future__ import annotations

import asyncio
import asyncio.queues
import inspect
import itertools
import runpy
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Literal

from pyconvis.schema import (
    CommEvent,
    EventKind,
    RuntimeMetadata,
    SpanState,
    StateSpan,
    TraceDocument,
    UnitRecord,
    coalesce_unit_spans,
    compute_metrics,
)

_INTERNAL_CORO_NAMES = frozenset(
    {
        "shutdown_asyncgens",
        "shutdown_default_executor",
        "_cancel_all_tasks",
    }
)


def _format_value(val: Any, max_len: int = 18) -> str:
    if val is None:
        return "None"
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, bytes):
        try:
            s = val.decode("utf-8", errors="replace")
        except Exception:
            s = repr(val)
    elif isinstance(val, str):
        s = val
    else:
        s = repr(val)
    s = s.replace("\n", "\\n")
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


class _AsyncEnvelope:
    __slots__ = ("eid", "from_unit", "send_ns", "channel", "preview", "item")

    def __init__(
        self,
        eid: str,
        from_unit: str,
        send_ns: int,
        channel: str,
        preview: str,
        item: Any,
    ) -> None:
        self.eid = eid
        self.from_unit = from_unit
        self.send_ns = send_ns
        self.channel = channel
        self.preview = preview
        self.item = item

    def __lt__(self, other: Any) -> bool:
        other_item = other.item if isinstance(other, _AsyncEnvelope) else other
        return bool(self.item < other_item)


class _TrackedCoroutine(Coroutine[Any, Any, Any]):
    """Coroutine proxy recording exact synchronous event-loop turns and await suspensions."""

    def __init__(
        self,
        coro: Coroutine[Any, Any, Any],
        unit_id: str,
        session: _AsyncioTraceSession,
    ) -> None:
        self._coro = coro
        self._unit_id = unit_id
        self._session = session
        self._last_exit_ns: int | None = None
        self._finished = False

    def __await__(self) -> Any:
        return self._coro.__await__()

    def _record_pre_turn_wait(self, enter_ns: int) -> None:
        if self._last_exit_ns is not None:
            wait_state, expected_wake_ns = self._session.consume_wait_state(
                self._unit_id
            )
            if enter_ns > self._last_exit_ns:
                if (
                    wait_state == "waiting_io"
                    and expected_wake_ns is not None
                    and expected_wake_ns > self._last_exit_ns
                    and (enter_ns - expected_wake_ns) >= 5_000_000
                ):
                    self._session.add_span(
                        self._unit_id,
                        "waiting_io",
                        self._last_exit_ns,
                        expected_wake_ns,
                    )
                    self._session.add_span(
                        self._unit_id,
                        "suspended_await",
                        expected_wake_ns,
                        enter_ns,
                        detail="loop_stalled",
                    )
                else:
                    self._session.add_span(
                        self._unit_id, wait_state, self._last_exit_ns, enter_ns
                    )
        else:
            self._session.consume_wait_state(self._unit_id)
            u = self._session.units.get(self._unit_id)
            if u is not None and enter_ns > u.start_ns:
                self._session.add_span(
                    self._unit_id, "suspended_await", u.start_ns, enter_ns
                )

    def _record_turn_cpu(self, enter_ns: int, exit_ns: int) -> None:
        dur = max(1, exit_ns - enter_ns)
        end_mark = enter_ns + dur
        state: SpanState = (
            "blocking_loop"
            if dur >= self._session.blocking_threshold_ns
            else "running_cpu"
        )
        self._session.add_span(self._unit_id, state, enter_ns, end_mark)
        self._last_exit_ns = end_mark

    def send(self, value: Any) -> Any:
        enter_ns = self._session.rel_ns()
        self._record_pre_turn_wait(enter_ns)
        prev_unit = self._session.active_unit_id
        self._session.active_unit_id = self._unit_id
        try:
            result = self._coro.send(value)
        except StopIteration as stop:
            exit_ns = self._session.rel_ns()
            self._record_turn_cpu(enter_ns, exit_ns)
            self._finished = True
            self._session.finish_unit(self._unit_id, exit_ns, stop.value)
            raise
        except BaseException:
            exit_ns = self._session.rel_ns()
            self._record_turn_cpu(enter_ns, exit_ns)
            self._finished = True
            self._session.finish_unit(self._unit_id, exit_ns, None)
            raise
        else:
            exit_ns = self._session.rel_ns()
            self._record_turn_cpu(enter_ns, exit_ns)
            return result
        finally:
            self._session.active_unit_id = prev_unit

    def throw(self, typ: Any, val: Any = None, tb: Any = None) -> Any:
        enter_ns = self._session.rel_ns()
        self._record_pre_turn_wait(enter_ns)
        prev_unit = self._session.active_unit_id
        self._session.active_unit_id = self._unit_id
        try:
            if val is None and tb is None:
                result = self._coro.throw(typ)
            else:
                result = self._coro.throw(typ, val, tb)
        except StopIteration as stop:
            exit_ns = self._session.rel_ns()
            self._record_turn_cpu(enter_ns, exit_ns)
            self._finished = True
            self._session.finish_unit(self._unit_id, exit_ns, stop.value)
            raise
        except BaseException:
            exit_ns = self._session.rel_ns()
            self._record_turn_cpu(enter_ns, exit_ns)
            self._finished = True
            self._session.finish_unit(self._unit_id, exit_ns, None)
            raise
        else:
            exit_ns = self._session.rel_ns()
            self._record_turn_cpu(enter_ns, exit_ns)
            return result
        finally:
            self._session.active_unit_id = prev_unit

    def close(self) -> None:
        self._coro.close()


class _AsyncioTraceSession:
    def __init__(
        self,
        *,
        title: str,
        layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
        blocking_threshold_ms: float = 15.0,
    ) -> None:
        self.title = title
        self.layout_hint = layout_hint
        self.blocking_threshold_ns = int(blocking_threshold_ms * 1_000_000)
        self.t0_ns = 0
        self.active = False
        self.active_unit_id: str | None = None

        self._unit_seq = itertools.count(1)
        self._event_seq = itertools.count(1)
        self._queue_seq = itertools.count(1)
        self._lock_seq = itertools.count(1)
        self._event_prim_seq = itertools.count(1)

        self.units: dict[str, UnitRecord] = {}
        self.tracked_coros: dict[str, _TrackedCoroutine] = {}
        self.task_to_unit: dict[int, str] = {}
        self.spans: list[StateSpan] = []
        self.events: list[CommEvent] = []
        self._pending_wait_state: dict[str, tuple[SpanState, int | None]] = {}

    def start_clock(self) -> None:
        self.t0_ns = time.perf_counter_ns()
        self.active = True

    def rel_ns(self) -> int:
        if not self.active:
            return 0
        return max(0, time.perf_counter_ns() - self.t0_ns)

    def current_unit(self) -> str:
        if self.active_unit_id is not None:
            return self.active_unit_id
        try:
            t = asyncio.current_task()
        except RuntimeError:
            t = None
        if t is not None:
            uid = self.task_to_unit.get(id(t))
            if uid is not None:
                return uid
        return "main"

    def set_wait_state(
        self,
        unit_id: str,
        state: SpanState,
        expected_wake_ns: int | None = None,
    ) -> None:
        if self.active:
            self._pending_wait_state[unit_id] = (state, expected_wake_ns)

    def consume_wait_state(self, unit_id: str) -> tuple[SpanState, int | None]:
        return self._pending_wait_state.pop(unit_id, ("suspended_await", None))

    def add_span(
        self,
        unit_id: str,
        state: SpanState,
        start_ns: int,
        end_ns: int,
        detail: str | None = None,
    ) -> None:
        if not self.active or end_ns <= start_ns:
            return
        self.spans.append(
            StateSpan(
                unit_id=unit_id,
                state=state,
                start_ns=start_ns,
                end_ns=end_ns,
                detail=detail,
            )
        )

    def add_event(
        self,
        *,
        kind: EventKind,
        from_unit: str,
        to_unit: str,
        channel: str,
        send_ns: int,
        recv_ns: int,
        value: str,
    ) -> None:
        if not self.active:
            return
        if from_unit not in self.units or to_unit not in self.units:
            return
        eid = f"ev_{next(self._event_seq)}"
        s = max(0, send_ns)
        r = max(s, recv_ns)
        self.events.append(
            CommEvent(
                id=eid,
                kind=kind,
                from_unit=from_unit,
                to_unit=to_unit,
                channel=channel,
                send_ns=s,
                recv_ns=r,
                value=value,
            )
        )

    def register_root(self, coro: Coroutine[Any, Any, Any]) -> _TrackedCoroutine:
        self.units["main"] = UnitRecord(
            id="main",
            name="main",
            kind="main",
            parent_id=None,
            depth=0,
            start_ns=0,
            end_ns=0,
            leaked=False,
        )
        tc = _TrackedCoroutine(coro, "main", self)
        self.tracked_coros["main"] = tc
        return tc

    def register_child(
        self,
        coro: Coroutine[Any, Any, Any],
        explicit_name: str | None = None,
    ) -> tuple[str, _TrackedCoroutine]:
        seq = next(self._unit_seq)
        uid = f"task_{seq}"
        parent_id = self.current_unit()
        if parent_id not in self.units:
            parent_id = "main"
        parent_depth = self.units[parent_id].depth
        raw_name = getattr(coro, "__name__", None) or getattr(
            coro, "__qualname__", "task"
        )
        if explicit_name and not explicit_name.startswith("Task-"):
            label = explicit_name
        else:
            short = raw_name.split(".")[-1]
            label = f"{short} #{seq}"

        now = self.rel_ns()
        self.units[uid] = UnitRecord(
            id=uid,
            name=label,
            kind="task",
            parent_id=parent_id,
            depth=parent_depth + 1,
            start_ns=now,
            end_ns=now,
            leaked=False,
        )
        tc = _TrackedCoroutine(coro, uid, self)
        self.tracked_coros[uid] = tc
        return uid, tc

    def register_helper_thread(
        self,
        parent_id: str,
        func_name: str,
        spawn_ns: int,
    ) -> str:
        seq = next(self._unit_seq)
        uid = f"thread_{seq}"
        if parent_id not in self.units:
            parent_id = "main"
        parent_depth = self.units[parent_id].depth
        short = func_name.split(".")[-1] if func_name else "work"
        label = f"thread:{short}"
        self.units[uid] = UnitRecord(
            id=uid,
            name=label,
            kind="thread",
            parent_id=parent_id,
            depth=parent_depth + 1,
            start_ns=spawn_ns,
            end_ns=spawn_ns,
            leaked=False,
        )
        return uid

    def finish_unit(self, unit_id: str, exit_ns: int, return_value: Any) -> None:
        u = self.units.get(unit_id)
        if u is None:
            return
        u.end_ns = max(u.start_ns, exit_ns)
        if (
            unit_id != "main"
            and not u.leaked
            and u.parent_id is not None
            and return_value is not None
            and isinstance(return_value, (int, float, str, bool))
        ):
            self.add_event(
                kind="task_result",
                from_unit=unit_id,
                to_unit=u.parent_id,
                channel="return",
                send_ns=exit_ns,
                recv_ns=exit_ns,
                value=_format_value(return_value),
            )

    def finalize_at_main_exit(self, loop: asyncio.AbstractEventLoop) -> int:
        end_ns = max(1, self.rel_ns())
        main_u = self.units.get("main")
        if main_u is not None:
            main_u.end_ns = max(main_u.end_ns, end_ns)

        # Snapshot any still-pending user tasks as leaked before cancelling them cleanly.
        pending_tasks: list[asyncio.Task[Any]] = []
        for task in asyncio.all_tasks(loop):
            uid = self.task_to_unit.get(id(task))
            if uid is None or uid == "main":
                continue
            if not task.done():
                u = self.units[uid]
                u.leaked = True
                u.end_ns = end_ns
                tc = self.tracked_coros.get(uid)
                if tc is not None and tc._last_exit_ns is not None and end_ns > tc._last_exit_ns:
                    wait_state, _ = self.consume_wait_state(uid)
                    self.add_span(uid, wait_state, tc._last_exit_ns, end_ns)
                elif u.end_ns > u.start_ns:
                    self.add_span(uid, "suspended_await", u.start_ns, end_ns)
                pending_tasks.append(task)

        self.active = False
        for t in pending_tasks:
            t.cancel()
        return end_ns

    def build_document(self, duration_ns: int) -> TraceDocument:
        dur = max(1, duration_ns)
        for u in self.units.values():
            u.start_ns = max(0, min(dur, u.start_ns))
            u.end_ns = max(u.start_ns, min(dur, u.end_ns))

        units_sorted = sorted(
            self.units.values(), key=lambda x: (0 if x.id == "main" else 1, x.start_ns, x.id)
        )
        clean_spans = coalesce_unit_spans(self.spans, duration_ns=dur)
        events_sorted = sorted(self.events, key=lambda e: (e.recv_ns, e.send_ns, e.id))
        for ev in events_sorted:
            ev.send_ns = max(0, min(dur, ev.send_ns))
            ev.recv_ns = max(ev.send_ns, min(dur, ev.recv_ns))

        metadata = RuntimeMetadata.current(
            title=self.title,
            model="asyncio",
            duration_ns=dur,
            layout_hint=self.layout_hint,
        )
        metrics = compute_metrics(
            duration_ns=dur,
            units=units_sorted,
            spans=clean_spans,
            events=events_sorted,
        )
        return TraceDocument(
            metadata=metadata,
            metrics=metrics,
            units=units_sorted,
            spans=clean_spans,
            events=events_sorted,
        )


def _make_patched_primitives(session: _AsyncioTraceSession, originals: dict[str, Any]) -> dict[str, Any]:
    orig_sleep = originals["sleep"]
    orig_to_thread = originals["to_thread"]
    orig_Queue = originals["Queue"]
    orig_LifoQueue = originals["LifoQueue"]
    orig_PriorityQueue = originals["PriorityQueue"]
    orig_Lock = originals["Lock"]
    orig_Event = originals["Event"]
    orig_Semaphore = originals["Semaphore"]

    async def traced_sleep(delay: float, result: Any = None) -> Any:
        uid = session.current_unit()
        wake_ns = session.rel_ns() + max(0, int(delay * 1_000_000_000))
        session.set_wait_state(uid, "waiting_io", expected_wake_ns=wake_ns)
        return await orig_sleep(delay, result)

    async def traced_to_thread(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        caller_uid = session.current_unit()
        spawn_ns = session.rel_ns()
        fname = getattr(func, "__name__", "work")
        thread_uid = session.register_helper_thread(caller_uid, fname, spawn_ns)
        session.set_wait_state(caller_uid, "suspended_await")

        def _bg_wrapper() -> tuple[Any, int, int, int]:
            bg_s = session.rel_ns()
            c0 = time.thread_time_ns()
            try:
                val = func(*args, **kwargs)
            finally:
                c1 = time.thread_time_ns()
                bg_e = session.rel_ns()
            return val, bg_s, bg_e, max(0, c1 - c0)

        val, bg_s, bg_e, cpu_ns = await orig_to_thread(_bg_wrapper)
        resume_ns = session.rel_ns()
        u = session.units.get(thread_uid)
        if u is not None:
            s_mark = max(u.start_ns, bg_s)
            e_mark = max(s_mark + 1, bg_e)
            u.end_ns = max(e_mark, resume_ns)
            wall_ns = max(1, e_mark - s_mark)
            bg_state: SpanState = (
                "running_cpu" if cpu_ns >= wall_ns * 0.35 else "waiting_io"
            )
            session.add_span(thread_uid, bg_state, u.start_ns, u.end_ns, "to_thread")
            session.add_event(
                kind="task_result",
                from_unit=thread_uid,
                to_unit=caller_uid,
                channel="to_thread",
                send_ns=e_mark,
                recv_ns=max(e_mark, resume_ns),
                value=_format_value(val) if val is not None else "done",
            )
        return val

    def _wrap_queue_cls(base_cls: type[Any], prefix: str) -> type[Any]:
        class TracedAsyncQueue(base_cls):  # type: ignore[valid-type,misc]
            def __init__(self, maxsize: int = 0) -> None:
                super().__init__(maxsize=maxsize)
                self._pyconvis_name = f"{prefix}#{next(session._queue_seq)}"

            def _put(self, item: Any) -> None:
                send_ns = session.rel_ns()
                env = _AsyncEnvelope(
                    eid=f"q_{id(self)}_{send_ns}",
                    from_unit=session.current_unit(),
                    send_ns=send_ns,
                    channel=self._pyconvis_name,
                    preview=_format_value(item),
                    item=item,
                )
                super()._put(env)

            def _get(self) -> Any:
                raw = super()._get()
                if isinstance(raw, _AsyncEnvelope):
                    recv_ns = session.rel_ns()
                    session.add_event(
                        kind="queue_message",
                        from_unit=raw.from_unit,
                        to_unit=session.current_unit(),
                        channel=raw.channel,
                        send_ns=raw.send_ns,
                        recv_ns=recv_ns,
                        value=raw.preview,
                    )
                    return raw.item
                return raw

            async def put(self, item: Any) -> None:
                uid = session.current_unit()
                if self.full():
                    session.set_wait_state(uid, "waiting_sync")
                await super().put(item)

            async def get(self) -> Any:
                uid = session.current_unit()
                if self.empty():
                    session.set_wait_state(uid, "waiting_sync")
                return await super().get()

        TracedAsyncQueue.__name__ = base_cls.__name__
        return TracedAsyncQueue

    class TracedAsyncLock(orig_Lock):  # type: ignore[valid-type,misc]
        def __init__(self) -> None:
            super().__init__()
            self._pyconvis_name = f"lock#{next(session._lock_seq)}"
            self._last_holder: str | None = None
            self._last_release_ns: int = 0
            self._current_holder: str | None = None

        async def acquire(self) -> bool:
            uid = session.current_unit()
            contended = self.locked()
            if contended:
                session.set_wait_state(uid, "waiting_sync")
            res = await super().acquire()
            acq_ns = session.rel_ns()
            if (
                contended
                and self._last_holder is not None
                and self._last_holder != uid
            ):
                session.add_event(
                    kind="lock_handoff",
                    from_unit=self._last_holder,
                    to_unit=uid,
                    channel=self._pyconvis_name,
                    send_ns=self._last_release_ns,
                    recv_ns=acq_ns,
                    value="lock",
                )
            self._current_holder = uid
            return res

        def release(self) -> None:
            self._last_holder = self._current_holder or session.current_unit()
            self._last_release_ns = session.rel_ns()
            self._current_holder = None
            super().release()

    class TracedAsyncEvent(orig_Event):  # type: ignore[valid-type,misc]
        def __init__(self) -> None:
            super().__init__()
            self._pyconvis_name = f"event#{next(session._event_prim_seq)}"
            self._setter_unit: str | None = None
            self._set_ns: int = 0

        def set(self) -> None:
            self._setter_unit = session.current_unit()
            self._set_ns = session.rel_ns()
            super().set()

        async def wait(self) -> Literal[True]:
            uid = session.current_unit()
            was_unset = not self.is_set()
            if was_unset:
                session.set_wait_state(uid, "waiting_sync")
            res = await super().wait()
            if was_unset and self._setter_unit is not None and self._setter_unit != uid:
                session.add_event(
                    kind="event_signal",
                    from_unit=self._setter_unit,
                    to_unit=uid,
                    channel=self._pyconvis_name,
                    send_ns=self._set_ns,
                    recv_ns=session.rel_ns(),
                    value="set",
                )
            return res

    class TracedAsyncSemaphore(orig_Semaphore):  # type: ignore[valid-type,misc]
        async def acquire(self) -> bool:
            uid = session.current_unit()
            if self.locked():
                session.set_wait_state(uid, "waiting_sync")
            return await super().acquire()

    return {
        "sleep": traced_sleep,
        "to_thread": traced_to_thread,
        "Queue": _wrap_queue_cls(orig_Queue, "q"),
        "LifoQueue": _wrap_queue_cls(orig_LifoQueue, "lifoq"),
        "PriorityQueue": _wrap_queue_cls(orig_PriorityQueue, "prioq"),
        "Lock": TracedAsyncLock,
        "Event": TracedAsyncEvent,
        "Semaphore": TracedAsyncSemaphore,
    }


def _execute_coro_with_session(
    coro: Coroutine[Any, Any, Any],
    session: _AsyncioTraceSession,
    orig_run: Callable[..., Any],
) -> tuple[Any, int]:
    duration_holder = [1]

    async def _root_driver() -> Any:
        loop = asyncio.get_running_loop()
        session.start_clock()
        root_tc = session.register_root(coro)
        root_task = asyncio.Task(root_tc, loop=loop)
        session.task_to_unit[id(root_task)] = "main"

        def _task_factory(
            lp: asyncio.AbstractEventLoop,
            child_coro: Any,
            **kwargs: Any,
        ) -> asyncio.Task[Any]:
            coro_name = getattr(child_coro, "__name__", "")
            if not session.active or coro_name in _INTERNAL_CORO_NAMES:
                return asyncio.Task(child_coro, loop=lp, **kwargs)
            explicit_name = kwargs.get("name")
            uid, tc = session.register_child(child_coro, explicit_name=explicit_name)
            task = asyncio.Task(tc, loop=lp, **kwargs)
            session.task_to_unit[id(task)] = uid
            return task

        loop.set_task_factory(_task_factory)
        try:
            return await root_task
        finally:
            loop.set_task_factory(None)
            duration_holder[0] = session.finalize_at_main_exit(loop)
            # Give cancelled leaked tasks one tick to settle cleanly without RuntimeWarnings.
            await asyncio.sleep(0)

    res = orig_run(_root_driver())
    return res, duration_holder[0]


def trace_asyncio(
    entry: Callable[[], Coroutine[Any, Any, Any]] | Coroutine[Any, Any, Any],
    *,
    title: str = "Asyncio Trace",
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    blocking_threshold_ms: float = 15.0,
) -> TraceDocument:
    """Execute an async callable or coroutine and return a validated TraceDocument."""
    session = _AsyncioTraceSession(
        title=title,
        layout_hint=layout_hint,
        blocking_threshold_ms=blocking_threshold_ms,
    )
    originals = {
        "sleep": asyncio.sleep,
        "to_thread": asyncio.to_thread,
        "Queue": asyncio.Queue,
        "LifoQueue": asyncio.LifoQueue,
        "PriorityQueue": asyncio.PriorityQueue,
        "Lock": asyncio.Lock,
        "Event": asyncio.Event,
        "Semaphore": asyncio.Semaphore,
        "run": asyncio.run,
    }
    patched = _make_patched_primitives(session, originals)
    for k, v in patched.items():
        setattr(asyncio, k, v)
    asyncio.queues.Queue = patched["Queue"]  # type: ignore[misc]
    asyncio.queues.LifoQueue = patched["LifoQueue"]  # type: ignore[misc]
    asyncio.queues.PriorityQueue = patched["PriorityQueue"]  # type: ignore[misc]

    try:
        coro = entry() if callable(entry) and not inspect.iscoroutine(entry) else entry
        _, dur_ns = _execute_coro_with_session(coro, session, originals["run"])  # type: ignore[arg-type]
    finally:
        for k, v in originals.items():
            setattr(asyncio, k, v)
        asyncio.queues.Queue = originals["Queue"]  # type: ignore[misc]
        asyncio.queues.LifoQueue = originals["LifoQueue"]  # type: ignore[misc]
        asyncio.queues.PriorityQueue = originals["PriorityQueue"]  # type: ignore[misc]

    return session.build_document(dur_ns)


def trace_asyncio_script(
    script_path: str | Path,
    *,
    title: str | None = None,
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    blocking_threshold_ms: float = 15.0,
) -> TraceDocument:
    """Run a Python script file, intercepting its asyncio.run(...) entry point."""
    path = Path(script_path).resolve()
    session = _AsyncioTraceSession(
        title=title or path.stem,
        layout_hint=layout_hint,
        blocking_threshold_ms=blocking_threshold_ms,
    )
    originals = {
        "sleep": asyncio.sleep,
        "to_thread": asyncio.to_thread,
        "Queue": asyncio.Queue,
        "LifoQueue": asyncio.LifoQueue,
        "PriorityQueue": asyncio.PriorityQueue,
        "Lock": asyncio.Lock,
        "Event": asyncio.Event,
        "Semaphore": asyncio.Semaphore,
        "run": asyncio.run,
    }
    patched = _make_patched_primitives(session, originals)
    duration_holder = [0]

    def _intercepted_run(main_coro: Coroutine[Any, Any, Any], **kwargs: Any) -> Any:
        res, dur_ns = _execute_coro_with_session(main_coro, session, originals["run"])
        duration_holder[0] = dur_ns
        return res

    for k, v in patched.items():
        setattr(asyncio, k, v)
    asyncio.run = _intercepted_run  # type: ignore[assignment]
    asyncio.queues.Queue = patched["Queue"]  # type: ignore[misc]
    asyncio.queues.LifoQueue = patched["LifoQueue"]  # type: ignore[misc]
    asyncio.queues.PriorityQueue = patched["PriorityQueue"]  # type: ignore[misc]

    old_sys_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        code_obj = compile(
            path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True
        )
        exec(code_obj, {"__name__": "__main__", "__file__": str(path)})
    finally:
        sys.path[:] = old_sys_path
        for k, v in originals.items():
            setattr(asyncio, k, v)
        asyncio.queues.Queue = originals["Queue"]  # type: ignore[misc]
        asyncio.queues.LifoQueue = originals["LifoQueue"]  # type: ignore[misc]
        asyncio.queues.PriorityQueue = originals["PriorityQueue"]  # type: ignore[misc]

    if duration_holder[0] == 0:
        raise RuntimeError(
            f"Script {path.name} did not invoke asyncio.run(...); use threading mode instead."
        )
    return session.build_document(duration_holder[0])
