"""Real runtime execution tracer for Python threading (with GIL and free-threaded no-GIL)."""

from __future__ import annotations

import itertools
import os
import queue
import runpy
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pyconvis.schema import (
    CommEvent,
    EventKind,
    RuntimeMetadata,
    SpanState,
    StateSpan,
    TraceDocument,
    UnitKind,
    UnitRecord,
    coalesce_unit_spans,
    compute_metrics,
)

_STDLIB_PREFIXES: tuple[str, ...] = (
    os.path.dirname(threading.__file__),
    os.path.dirname(__file__),
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


def _is_internal_file(filename: str) -> bool:
    if not filename or filename.startswith("<"):
        return False
    return filename.startswith(_STDLIB_PREFIXES)


class _ThreadEnvelope:
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
        other_item = other.item if isinstance(other, _ThreadEnvelope) else other
        return bool(self.item < other_item)


class _ThreadLocalBuffer:
    """Per-thread lock-free trace state mutated exclusively by its owning OS thread."""

    __slots__ = (
        "unit_id",
        "mode",
        "jump_counter",
        "cpu_marks",
        "explicit_spans",
        "events",
    )

    def __init__(self, unit_id: str, start_ns: int) -> None:
        self.unit_id = unit_id
        self.mode: Literal["cpu", "wait"] = "cpu"
        self.jump_counter = 0
        self.cpu_marks: list[int] = [start_ns]
        self.explicit_spans: list[tuple[SpanState, int, int, str | None]] = []
        self.events: list[tuple[EventKind, str, str, str, int, int, str]] = []


class _ThreadingTraceSession:
    def __init__(
        self,
        *,
        title: str,
        layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
        switch_interval_s: float = 0.002,
        gil_gap_ns: int = 1_800_000,
        jump_subsample: int = 4096,
        record_gil_handoffs: bool = False,
    ) -> None:
        self.title = title
        self.layout_hint = layout_hint
        self.switch_interval_s = switch_interval_s
        self.gil_gap_ns = gil_gap_ns
        self.jump_subsample = jump_subsample
        self.record_gil_handoffs = record_gil_handoffs

        gil_fn = getattr(sys, "_is_gil_enabled", None)
        self.gil_enabled = bool(gil_fn()) if callable(gil_fn) else True

        self.t0_ns = 0
        self.active = False

        self.tls = threading.local()
        self._reg_lock = threading._allocate_lock()  # Raw C lock only touched on thread start/stop
        self._unit_seq = itertools.count(1)
        self._queue_seq = itertools.count(1)
        self._lock_seq = itertools.count(1)
        self._event_prim_seq = itertools.count(1)

        self.units: dict[str, UnitRecord] = {}
        self.buffers: dict[str, _ThreadLocalBuffer] = {}
        self.live_threads: list[tuple[str, threading.Thread]] = []

    def start_clock(self) -> None:
        self.t0_ns = time.perf_counter_ns()
        self.active = True
        main_rec = UnitRecord(
            id="main",
            name="main",
            kind="main",
            parent_id=None,
            depth=0,
            start_ns=0,
            end_ns=0,
            leaked=False,
        )
        buf = _ThreadLocalBuffer("main", 0)
        self.units["main"] = main_rec
        self.buffers["main"] = buf
        self.tls.buf = buf

    def rel_ns(self) -> int:
        if not self.active:
            return 0
        return max(0, time.perf_counter_ns() - self.t0_ns)

    def current_buf(self) -> _ThreadLocalBuffer | None:
        return getattr(self.tls, "buf", None)

    def current_unit_id(self) -> str:
        buf = getattr(self.tls, "buf", None)
        return buf.unit_id if buf is not None else "main"

    def register_thread_init(
        self,
        explicit_name: str | None,
        target: Any,
    ) -> tuple[str, str]:
        parent_id = self.current_unit_id()
        with self._reg_lock:
            seq = next(self._unit_seq)
            uid = f"thread_{seq}"
            if parent_id not in self.units:
                parent_id = "main"
            depth = self.units[parent_id].depth + 1

            target_name = getattr(target, "__name__", None) or "worker"
            if explicit_name and not explicit_name.startswith("Thread-"):
                label = explicit_name
            else:
                label = f"{target_name} #{seq}"

            now = self.rel_ns()
            self.units[uid] = UnitRecord(
                id=uid,
                name=label,
                kind="thread",
                parent_id=parent_id,
                depth=depth,
                start_ns=now,
                end_ns=now,
                leaked=False,
            )
            return uid, label

    def on_thread_start(self, uid: str, thread_obj: threading.Thread) -> _ThreadLocalBuffer:
        now = self.rel_ns()
        buf = _ThreadLocalBuffer(uid, now)
        self.tls.buf = buf
        with self._reg_lock:
            u = self.units[uid]
            u.start_ns = now
            u.end_ns = now
            self.buffers[uid] = buf
            self.live_threads.append((uid, thread_obj))
        return buf

    def on_thread_finish(self, uid: str, buf: _ThreadLocalBuffer) -> None:
        now = self.rel_ns()
        buf.cpu_marks.append(now)
        with self._reg_lock:
            if self.active and uid in self.units:
                self.units[uid].end_ns = max(self.units[uid].start_ns, now)

    def record_wait_span(
        self,
        buf: _ThreadLocalBuffer | None,
        state: SpanState,
        start_ns: int,
        end_ns: int,
        detail: str | None = None,
    ) -> None:
        if buf is None or not self.active:
            return
        if end_ns > start_ns:
            buf.explicit_spans.append((state, start_ns, end_ns, detail))
        buf.cpu_marks.append(end_ns)

    def finalize_and_build(self) -> TraceDocument:
        end_ns = max(1, self.rel_ns())
        self.active = False

        with self._reg_lock:
            self.units["main"].end_ns = end_ns
            main_buf = self.buffers.get("main")
            if main_buf is not None:
                main_buf.cpu_marks.append(end_ns)

            for uid, thr in self.live_threads:
                u = self.units[uid]
                if thr.is_alive():
                    u.leaked = True
                    u.end_ns = end_ns
                else:
                    u.end_ns = max(u.start_ns, min(end_ns, u.end_ns))

        return self._reconstruct_document(end_ns)

    def _first_other_mark_between(
        self,
        exclude_uid: str,
        sorted_marks_by_unit: dict[str, list[int]],
        a_ns: int,
        b_ns: int,
    ) -> int | None:
        import bisect

        earliest: int | None = None
        for uid, marks in sorted_marks_by_unit.items():
            if uid == exclude_uid or not marks:
                continue
            idx = bisect.bisect_right(marks, a_ns)
            if idx < len(marks) and marks[idx] < b_ns:
                cand = marks[idx]
                if earliest is None or cand < earliest:
                    earliest = cand
        return earliest

    def _reconstruct_document(self, duration_ns: int) -> TraceDocument:
        dur = max(1, duration_ns)

        # Snapshot marks safely (leaked threads may still be running)
        marks_snapshot: dict[str, list[int]] = {}
        explicit_snapshot: dict[str, list[tuple[SpanState, int, int, str | None]]] = {}
        raw_events: list[tuple[EventKind, str, str, str, int, int, str]] = []

        for uid, buf in list(self.buffers.items()):
            raw_m = list(buf.cpu_marks)
            marks_snapshot[uid] = sorted({max(0, min(dur, m)) for m in raw_m})
            explicit_snapshot[uid] = list(buf.explicit_spans)
            raw_events.extend(list(buf.events))

        all_spans: list[StateSpan] = []

        for uid, u in self.units.items():
            u.start_ns = max(0, min(dur, u.start_ns))
            u.end_ns = max(u.start_ns, min(dur, u.end_ns))
            if u.end_ns <= u.start_ns:
                continue

            exp = sorted(
                [
                    (st, max(u.start_ns, s), min(u.end_ns, e), det)
                    for st, s, e, det in explicit_snapshot.get(uid, [])
                    if min(u.end_ns, e) > max(u.start_ns, s)
                ],
                key=lambda item: (item[1], item[2]),
            )

            # Merge overlapping explicit waits and carve remaining CPU windows
            merged_waits: list[tuple[SpanState, int, int, str | None]] = []
            for st, s, e, det in exp:
                if not merged_waits or s >= merged_waits[-1][2]:
                    merged_waits.append((st, s, e, det))
                else:
                    prev_st, prev_s, prev_e, prev_det = merged_waits[-1]
                    if e > prev_e:
                        merged_waits[-1] = (prev_st, prev_s, e, prev_det or det)

            cpu_windows: list[tuple[int, int]] = []
            cursor = u.start_ns
            for st, s, e, det in merged_waits:
                if s > cursor:
                    cpu_windows.append((cursor, s))
                if (
                    self.gil_enabled
                    and st == "waiting_io"
                    and det is not None
                    and det.startswith("sleep:")
                ):
                    try:
                        wake_ns = int(det.split(":", 1)[1])
                    except ValueError:
                        wake_ns = e
                    if (
                        s < wake_ns < e
                        and (e - wake_ns) >= self.gil_gap_ns
                        and self._first_other_mark_between(
                            uid, marks_snapshot, wake_ns, e
                        )
                        is not None
                    ):
                        all_spans.append(
                            StateSpan(
                                unit_id=uid,
                                state="waiting_io",
                                start_ns=s,
                                end_ns=wake_ns,
                                detail="sleep",
                            )
                        )
                        all_spans.append(
                            StateSpan(
                                unit_id=uid,
                                state="waiting_gil",
                                start_ns=wake_ns,
                                end_ns=e,
                                detail="gil_reacquire",
                            )
                        )
                    else:
                        all_spans.append(
                            StateSpan(
                                unit_id=uid,
                                state="waiting_io",
                                start_ns=s,
                                end_ns=e,
                                detail="sleep",
                            )
                        )
                else:
                    all_spans.append(
                        StateSpan(unit_id=uid, state=st, start_ns=s, end_ns=e, detail=det)
                    )
                cursor = max(cursor, e)
            if u.end_ns > cursor:
                cpu_windows.append((cursor, u.end_ns))

            unit_marks = marks_snapshot.get(uid, [])
            import bisect

            for w_start, w_end in cpu_windows:
                if w_end <= w_start:
                    continue
                if not self.gil_enabled:
                    all_spans.append(
                        StateSpan(
                            unit_id=uid,
                            state="running_cpu",
                            start_ns=w_start,
                            end_ns=w_end,
                        )
                    )
                    continue

                lo = bisect.bisect_left(unit_marks, w_start)
                hi = bisect.bisect_right(unit_marks, w_end)
                pts = [w_start]
                for m in unit_marks[lo:hi]:
                    if m > pts[-1]:
                        pts.append(m)
                if w_end > pts[-1]:
                    pts.append(w_end)

                seg_start = pts[0]
                for i in range(len(pts) - 1):
                    a, b = pts[i], pts[i + 1]
                    gap = b - a
                    if gap >= self.gil_gap_ns:
                        handoff_ns = self._first_other_mark_between(
                            uid, marks_snapshot, a, b
                        )
                        if handoff_ns is not None:
                            if handoff_ns > seg_start:
                                all_spans.append(
                                    StateSpan(
                                        unit_id=uid,
                                        state="running_cpu",
                                        start_ns=seg_start,
                                        end_ns=handoff_ns,
                                    )
                                )
                            all_spans.append(
                                StateSpan(
                                    unit_id=uid,
                                    state="waiting_gil",
                                    start_ns=handoff_ns,
                                    end_ns=b,
                                )
                            )
                            seg_start = b
                if w_end > seg_start:
                    all_spans.append(
                        StateSpan(
                            unit_id=uid,
                            state="running_cpu",
                            start_ns=seg_start,
                            end_ns=w_end,
                        )
                    )

            if u.leaked and not merged_waits and len(marks_snapshot.get(uid, [])) <= 2:
                # Idle/sleeping leaked thread fallback handled by explicit spans; keep clean.
                pass

        units_sorted = sorted(
            self.units.values(),
            key=lambda x: (0 if x.id == "main" else 1, x.start_ns, x.id),
        )
        clean_spans = coalesce_unit_spans(all_spans, duration_ns=dur)

        if self.gil_enabled and self.record_gil_handoffs:
            cpu_slices = sorted(
                [
                    sp
                    for sp in clean_spans
                    if sp.state == "running_cpu" and (sp.end_ns - sp.start_ns) >= 200_000
                ],
                key=lambda sp: (sp.start_ns, sp.end_ns),
            )
            for idx in range(len(cpu_slices) - 1):
                prev_sp = cpu_slices[idx]
                next_sp = cpu_slices[idx + 1]
                if prev_sp.unit_id == next_sp.unit_id:
                    continue
                if next_sp.start_ns - prev_sp.end_ns <= 10_000_000:
                    send_ns = min(prev_sp.end_ns, next_sp.start_ns)
                    recv_ns = max(send_ns, next_sp.start_ns)
                    raw_events.append(
                        (
                            "gil_handoff",
                            prev_sp.unit_id,
                            next_sp.unit_id,
                            "GIL",
                            send_ns,
                            recv_ns,
                            "GIL",
                        )
                    )

        events: list[CommEvent] = []
        raw_events.sort(key=lambda item: (item[5], item[4]))
        for idx, (kind, f_u, t_u, ch, s_ns, r_ns, val) in enumerate(raw_events, start=1):
            if f_u not in self.units or t_u not in self.units:
                continue
            s_c = max(0, min(dur, s_ns))
            r_c = max(s_c, min(dur, r_ns))
            events.append(
                CommEvent(
                    id=f"ev_{idx}",
                    kind=kind,
                    from_unit=f_u,
                    to_unit=t_u,
                    channel=ch,
                    send_ns=s_c,
                    recv_ns=r_c,
                    value=val,
                )
            )

        metadata = RuntimeMetadata.current(
            title=self.title,
            model="threading",
            duration_ns=dur,
            layout_hint=self.layout_hint,
        )
        metadata.switch_interval_s = self.switch_interval_s
        metrics = compute_metrics(
            duration_ns=dur,
            units=units_sorted,
            spans=clean_spans,
            events=events,
        )
        return TraceDocument(
            metadata=metadata,
            metrics=metrics,
            units=units_sorted,
            spans=clean_spans,
            events=events,
        )


def _install_monitoring(
    session: _ThreadingTraceSession, orig_Thread: Any, orig_Event: Any, orig_sleep: Any
) -> tuple[int | None, Any, Any]:
    """Pulse PEP 669 JUMP sampling at ~0.35ms intervals for <2% overhead GIL slice capture."""
    if not session.gil_enabled:
        return None, None, None

    tool_id: int | None = None
    for candidate in (
        sys.monitoring.PROFILER_ID,
        sys.monitoring.OPTIMIZER_ID,
        sys.monitoring.COVERAGE_ID,
    ):
        try:
            sys.monitoring.use_tool_id(candidate, "pyconvis_threading")
            tool_id = candidate
            break
        except ValueError:
            continue

    if tool_id is None:
        return None, None, None

    tls = session.tls
    stop_ev = orig_Event()
    jump_ev = sys.monitoring.events.JUMP

    pulse_budget = [0]

    def _on_jump(code: Any, instruction_offset: int, destination_offset: int) -> Any:
        if destination_offset >= instruction_offset:
            return None
        buf: _ThreadLocalBuffer | None = getattr(tls, "buf", None)
        if buf is None or buf.mode != "cpu" or not session.active:
            return None
        c = buf.jump_counter + 1
        if c < 512:
            buf.jump_counter = c
            return None
        buf.jump_counter = 0
        buf.cpu_marks.append(time.perf_counter_ns() - session.t0_ns)
        rem = pulse_budget[0] - 1
        if rem <= 0:
            sys.monitoring.set_events(tool_id, 0)
        else:
            pulse_budget[0] = rem
        return None

    sys.monitoring.register_callback(tool_id, jump_ev, _on_jump)

    def _pulse_loop() -> None:
        while not stop_ev.is_set() and session.active:
            pulse_budget[0] = 8
            sys.monitoring.set_events(tool_id, jump_ev)
            orig_sleep(0.0004)

    pulse_thread = orig_Thread(target=_pulse_loop, name="_pyconvis_pulse", daemon=True)
    pulse_thread.start()
    return tool_id, stop_ev, pulse_thread


def _uninstall_monitoring(
    handle: tuple[int | None, threading.Event | None, Any],
) -> None:
    tool_id, stop_ev, pulse_thread = handle
    if stop_ev is not None:
        stop_ev.set()
    if pulse_thread is not None:
        pulse_thread.join(timeout=0.05)
    if tool_id is None:
        return
    try:
        sys.monitoring.set_events(tool_id, 0)
        sys.monitoring.register_callback(tool_id, sys.monitoring.events.JUMP, None)
        sys.monitoring.free_tool_id(tool_id)
    except Exception:
        pass


def _run_with_threading_patches(
    fn: Callable[[], Any],
    session: _ThreadingTraceSession,
) -> TraceDocument:
    orig_Thread = threading.Thread
    orig_Lock = threading.Lock
    orig_Event = threading.Event
    orig_Semaphore = threading.Semaphore
    orig_sleep = time.sleep
    orig_Queue = queue.Queue
    orig_LifoQueue = queue.LifoQueue
    orig_PriorityQueue = queue.PriorityQueue
    orig_SimpleQueue = queue.SimpleQueue
    orig_switch = sys.getswitchinterval()

    class TracedThread(orig_Thread):  # type: ignore[valid-type,misc]
        def __init__(
            self,
            group: Any = None,
            target: Any = None,
            name: str | None = None,
            args: tuple[Any, ...] = (),
            kwargs: dict[str, Any] | None = None,
            *,
            daemon: bool | None = None,
        ) -> None:
            uid, clean_name = session.register_thread_init(name, target)
            self._pyconvis_uid = uid
            super().__init__(
                group=group,
                target=target,
                name=clean_name,
                args=args,
                kwargs=kwargs,
                daemon=daemon,
            )

        def run(self) -> None:
            buf = session.on_thread_start(self._pyconvis_uid, self)
            try:
                super().run()
            finally:
                session.on_thread_finish(self._pyconvis_uid, buf)

        def join(self, timeout: float | None = None) -> None:
            caller_buf = session.current_buf()
            if caller_buf is not None and self.is_alive() and session.active:
                s_ns = session.rel_ns()
                prev_mode = caller_buf.mode
                caller_buf.mode = "wait"
                try:
                    super().join(timeout=timeout)
                finally:
                    caller_buf.mode = prev_mode
                    e_ns = session.rel_ns()
                    if e_ns - s_ns >= 40_000:
                        session.record_wait_span(
                            caller_buf, "waiting_sync", s_ns, e_ns, "join"
                        )
            else:
                super().join(timeout=timeout)

    class TracedLock:
        def __init__(self, name: str) -> None:
            self._lock = orig_Lock()
            self._name = name
            self._last_holder: str | None = None
            self._last_release_ns: int = 0
            self._current_holder: str | None = None

        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            buf = session.current_buf()
            uid = buf.unit_id if buf is not None else "main"
            if not blocking:
                ok = self._lock.acquire(False)
                if ok:
                    self._current_holder = uid
                return ok

            # Fast non-blocking probe to detect true contention cleanly
            if self._lock.acquire(False):
                self._current_holder = uid
                return True

            s_ns = session.rel_ns()
            prev_mode = buf.mode if buf is not None else "cpu"
            if buf is not None:
                buf.mode = "wait"
            try:
                ok = (
                    self._lock.acquire(True, timeout)
                    if timeout >= 0
                    else self._lock.acquire(True)
                )
            finally:
                if buf is not None:
                    buf.mode = prev_mode
            e_ns = session.rel_ns()
            session.record_wait_span(buf, "waiting_sync", s_ns, e_ns, self._name)
            if ok:
                prev_holder = self._last_holder
                rel_ns = self._last_release_ns
                self._current_holder = uid
                if buf is not None and prev_holder is not None and prev_holder != uid:
                    buf.events.append(
                        (
                            "lock_handoff",
                            prev_holder,
                            uid,
                            self._name,
                            rel_ns if rel_ns > 0 else s_ns,
                            e_ns,
                            "lock",
                        )
                    )
            return ok

        def release(self) -> None:
            uid = session.current_unit_id()
            self._last_holder = self._current_holder or uid
            self._last_release_ns = session.rel_ns()
            self._current_holder = None
            self._lock.release()

        def locked(self) -> bool:
            return self._lock.locked()

        def __enter__(self) -> bool:
            return self.acquire()

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            self.release()

    def _patched_lock_factory() -> Any:
        try:
            caller_file = sys._getframe(1).f_code.co_filename
        except ValueError:
            caller_file = ""
        if _is_internal_file(caller_file):
            return orig_Lock()
        with session._reg_lock:
            lname = f"lock#{next(session._lock_seq)}"
        return TracedLock(lname)

    class TracedEvent(orig_Event):  # type: ignore[valid-type,misc]
        def __init__(self) -> None:
            super().__init__()
            with session._reg_lock:
                self._pyconvis_name = f"event#{next(session._event_prim_seq)}"
            self._setter_unit: str | None = None
            self._set_ns: int = 0

        def set(self) -> None:
            self._setter_unit = session.current_unit_id()
            self._set_ns = session.rel_ns()
            super().set()

        def wait(self, timeout: float | None = None) -> bool:
            if self.is_set():
                return True
            buf = session.current_buf()
            uid = buf.unit_id if buf is not None else "main"
            s_ns = session.rel_ns()
            prev_mode = buf.mode if buf is not None else "cpu"
            if buf is not None:
                buf.mode = "wait"
            try:
                ok = super().wait(timeout=timeout)
            finally:
                if buf is not None:
                    buf.mode = prev_mode
            e_ns = session.rel_ns()
            session.record_wait_span(buf, "waiting_sync", s_ns, e_ns, self._pyconvis_name)
            if (
                ok
                and buf is not None
                and self._setter_unit is not None
                and self._setter_unit != uid
            ):
                buf.events.append(
                    (
                        "event_signal",
                        self._setter_unit,
                        uid,
                        self._pyconvis_name,
                        self._set_ns,
                        e_ns,
                        "set",
                    )
                )
            return ok

    class TracedSemaphore(orig_Semaphore):  # type: ignore[valid-type,misc]
        def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
            if not blocking:
                return super().acquire(blocking=False)
            if super().acquire(blocking=False):
                return True
            buf = session.current_buf()
            s_ns = session.rel_ns()
            prev_mode = buf.mode if buf is not None else "cpu"
            if buf is not None:
                buf.mode = "wait"
            try:
                ok = super().acquire(blocking=True, timeout=timeout)
            finally:
                if buf is not None:
                    buf.mode = prev_mode
            e_ns = session.rel_ns()
            session.record_wait_span(buf, "waiting_sync", s_ns, e_ns, "semaphore")
            return ok

    def _patched_sleep(secs: float) -> None:
        buf = session.current_buf()
        if buf is None or not session.active or secs <= 0:
            orig_sleep(secs)
            return
        s_ns = session.rel_ns()
        wake_ns = s_ns + max(0, int(secs * 1_000_000_000))
        prev_mode = buf.mode
        buf.mode = "wait"
        try:
            orig_sleep(secs)
        finally:
            buf.mode = prev_mode
        e_ns = session.rel_ns()
        session.record_wait_span(buf, "waiting_io", s_ns, e_ns, f"sleep:{wake_ns}")

    def _patched_event_factory() -> Any:
        try:
            caller_file = sys._getframe(1).f_code.co_filename
        except ValueError:
            caller_file = ""
        if _is_internal_file(caller_file):
            return orig_Event()
        return TracedEvent()

    def _wrap_thread_queue_cls(base_cls: type[Any], prefix: str) -> type[Any]:
        class TracedThreadQueue(base_cls):  # type: ignore[valid-type,misc]
            def __init__(self, maxsize: int = 0) -> None:
                super().__init__(maxsize=maxsize)
                with session._reg_lock:
                    self._pyconvis_name = f"{prefix}#{next(session._queue_seq)}"

            def _put(self, item: Any) -> None:
                uid = session.current_unit_id()
                send_ns = session.rel_ns()
                env = _ThreadEnvelope(
                    eid=f"q_{id(self)}_{send_ns}",
                    from_unit=uid,
                    send_ns=send_ns,
                    channel=self._pyconvis_name,
                    preview=_format_value(item),
                    item=item,
                )
                super()._put(env)

            def _get(self) -> Any:
                raw = super()._get()
                if isinstance(raw, _ThreadEnvelope):
                    buf = session.current_buf()
                    uid = buf.unit_id if buf is not None else "main"
                    recv_ns = session.rel_ns()
                    if buf is not None:
                        buf.events.append(
                            (
                                "queue_message",
                                raw.from_unit,
                                uid,
                                raw.channel,
                                raw.send_ns,
                                recv_ns,
                                raw.preview,
                            )
                        )
                    return raw.item
                return raw

            def put(
                self, item: Any, block: bool = True, timeout: float | None = None
            ) -> None:
                buf = session.current_buf()
                send_ns = session.rel_ns()
                prev_mode = buf.mode if buf is not None else "cpu"
                if buf is not None and block:
                    buf.mode = "wait"
                try:
                    super().put(item, block=block, timeout=timeout)
                finally:
                    if buf is not None:
                        buf.mode = prev_mode
                end_ns = session.rel_ns()
                if buf is not None and (end_ns - send_ns) >= 60_000:
                    session.record_wait_span(
                        buf, "waiting_sync", send_ns, end_ns, self._pyconvis_name
                    )

            def get(self, block: bool = True, timeout: float | None = None) -> Any:
                buf = session.current_buf()
                wait_start = session.rel_ns()
                prev_mode = buf.mode if buf is not None else "cpu"
                if buf is not None and block:
                    buf.mode = "wait"
                try:
                    item = super().get(block=block, timeout=timeout)
                finally:
                    if buf is not None:
                        buf.mode = prev_mode
                recv_ns = session.rel_ns()
                if buf is not None and (recv_ns - wait_start) >= 60_000:
                    session.record_wait_span(
                        buf, "waiting_sync", wait_start, recv_ns, self._pyconvis_name
                    )
                return item

        TracedThreadQueue.__name__ = base_cls.__name__
        return TracedThreadQueue

    class TracedSimpleQueue(orig_SimpleQueue):  # type: ignore[valid-type,misc]
        def __init__(self) -> None:
            super().__init__()
            with session._reg_lock:
                self._pyconvis_name = f"sq#{next(session._queue_seq)}"

        def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
            uid = session.current_unit_id()
            send_ns = session.rel_ns()
            env = _ThreadEnvelope(
                eid=f"sq_{id(self)}_{send_ns}",
                from_unit=uid,
                send_ns=send_ns,
                channel=self._pyconvis_name,
                preview=_format_value(item),
                item=item,
            )
            super().put(env, block=block, timeout=timeout)

        def put_nowait(self, item: Any) -> None:
            self.put(item, block=False)

        def get(self, block: bool = True, timeout: float | None = None) -> Any:
            buf = session.current_buf()
            uid = buf.unit_id if buf is not None else "main"
            s_ns = session.rel_ns()
            prev_mode = buf.mode if buf is not None else "cpu"
            if buf is not None and block:
                buf.mode = "wait"
            try:
                raw = super().get(block=block, timeout=timeout)
            finally:
                if buf is not None:
                    buf.mode = prev_mode
            r_ns = session.rel_ns()
            if buf is not None and (r_ns - s_ns) >= 60_000:
                session.record_wait_span(buf, "waiting_sync", s_ns, r_ns, self._pyconvis_name)
            if isinstance(raw, _ThreadEnvelope):
                if buf is not None:
                    buf.events.append(
                        (
                            "queue_message",
                            raw.from_unit,
                            uid,
                            raw.channel,
                            raw.send_ns,
                            r_ns,
                            raw.preview,
                        )
                    )
                return raw.item
            return raw

    sys.setswitchinterval(session.switch_interval_s)
    threading.Thread = TracedThread  # type: ignore[misc]
    threading.Lock = _patched_lock_factory  # type: ignore[assignment]
    threading.Event = _patched_event_factory  # type: ignore[assignment]
    threading.Semaphore = TracedSemaphore  # type: ignore[misc]
    time.sleep = _patched_sleep
    queue.Queue = _wrap_thread_queue_cls(orig_Queue, "q")  # type: ignore[misc]
    queue.LifoQueue = _wrap_thread_queue_cls(orig_LifoQueue, "lifoq")  # type: ignore[misc]
    queue.PriorityQueue = _wrap_thread_queue_cls(orig_PriorityQueue, "prioq")  # type: ignore[misc]
    queue.SimpleQueue = TracedSimpleQueue  # type: ignore[misc]

    session.start_clock()
    mon_handle = _install_monitoring(session, orig_Thread, orig_Event, orig_sleep)
    try:
        fn()
    finally:
        _uninstall_monitoring(mon_handle)
        threading.Thread = orig_Thread  # type: ignore[misc]
        threading.Lock = orig_Lock  # type: ignore[assignment]
        threading.Event = orig_Event  # type: ignore[misc]
        threading.Semaphore = orig_Semaphore  # type: ignore[misc]
        time.sleep = orig_sleep
        queue.Queue = orig_Queue  # type: ignore[misc]
        queue.LifoQueue = orig_LifoQueue  # type: ignore[misc]
        queue.PriorityQueue = orig_PriorityQueue  # type: ignore[misc]
        queue.SimpleQueue = orig_SimpleQueue  # type: ignore[misc]
        sys.setswitchinterval(orig_switch)
        # Unused import guard: socket stays available for future extensions
        _ = socket

    return session.finalize_and_build()


def trace_threading(
    entry: Callable[[], Any],
    *,
    title: str = "Threading Trace",
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    switch_interval_s: float = 0.002,
    record_gil_handoffs: bool = False,
) -> TraceDocument:
    """Execute a callable under threading + GIL/no-GIL tracing and return TraceDocument."""
    session = _ThreadingTraceSession(
        title=title,
        layout_hint=layout_hint,
        switch_interval_s=switch_interval_s,
        record_gil_handoffs=record_gil_handoffs,
    )
    return _run_with_threading_patches(entry, session)


def trace_threading_script(
    script_path: str | Path,
    *,
    title: str | None = None,
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    switch_interval_s: float = 0.002,
    record_gil_handoffs: bool = False,
) -> TraceDocument:
    """Execute a Python script file under threading + GIL/no-GIL tracing."""
    path = Path(script_path).resolve()
    session = _ThreadingTraceSession(
        title=title or path.stem,
        layout_hint=layout_hint,
        switch_interval_s=switch_interval_s,
        record_gil_handoffs=record_gil_handoffs,
    )

    def _run_file() -> None:
        old_sys_path = list(sys.path)
        sys.path.insert(0, str(path.parent))
        try:
            code_obj = compile(
                path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True
            )
            exec(code_obj, {"__name__": "__main__", "__file__": str(path)})
        finally:
            sys.path[:] = old_sys_path

    return _run_with_threading_patches(_run_file, session)
