"""Unified JSON trace schema, validation, span coalescing, and educational telemetry."""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

ConcurrencyModel = Literal["asyncio", "threading"]
UnitKind = Literal["main", "task", "thread"]
SpanState = Literal[
    "running_cpu",
    "waiting_gil",
    "suspended_await",
    "waiting_io",
    "waiting_sync",
    "blocking_loop",
]
EventKind = Literal[
    "queue_message",
    "lock_handoff",
    "gil_handoff",
    "event_signal",
    "task_result",
]

VALID_STATES: frozenset[str] = frozenset(
    {
        "running_cpu",
        "waiting_gil",
        "suspended_await",
        "waiting_io",
        "waiting_sync",
        "blocking_loop",
    }
)
VALID_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "queue_message",
        "lock_handoff",
        "gil_handoff",
        "event_signal",
        "task_result",
    }
)


@dataclass
class RuntimeMetadata:
    title: str
    model: ConcurrencyModel
    python_version: str
    python_build: str
    gil_enabled: bool
    cpu_count: int
    duration_ns: int
    switch_interval_s: float | None = None
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial"

    @classmethod
    def current(
        cls,
        *,
        title: str,
        model: ConcurrencyModel,
        duration_ns: int,
        layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    ) -> RuntimeMetadata:
        gil_fn = getattr(sys, "_is_gil_enabled", None)
        gil_enabled = bool(gil_fn()) if callable(gil_fn) else True
        return cls(
            title=title,
            model=model,
            python_version=platform.python_version(),
            python_build=sys.version.split("\n")[0],
            gil_enabled=gil_enabled,
            cpu_count=os.cpu_count() or 1,
            duration_ns=max(1, int(duration_ns)),
            switch_interval_s=sys.getswitchinterval() if model == "threading" else None,
            layout_hint=layout_hint,
        )


@dataclass
class UnitRecord:
    id: str
    name: str
    kind: UnitKind
    parent_id: str | None
    depth: int
    start_ns: int
    end_ns: int
    leaked: bool = False


@dataclass
class StateSpan:
    unit_id: str
    state: SpanState
    start_ns: int
    end_ns: int
    detail: str | None = None


@dataclass
class CommEvent:
    id: str
    kind: EventKind
    from_unit: str
    to_unit: str
    channel: str
    send_ns: int
    recv_ns: int
    value: str = ""


@dataclass
class TraceMetrics:
    total_units: int
    worker_units: int
    leaked_units: int
    message_count: int
    sync_handoff_count: int
    duration_ms: float
    total_cpu_ms: float
    total_gil_wait_ms: float
    total_io_wait_ms: float
    total_sync_wait_ms: float
    total_await_wait_ms: float
    total_blocking_loop_ms: float
    effective_parallelism: float
    peak_parallel_cpu_units: int
    peak_concurrent_units: int
    gil_wait_pct: float
    cpu_active_pct: float


@dataclass
class TraceDocument:
    metadata: RuntimeMetadata
    metrics: TraceMetrics
    units: list[UnitRecord]
    spans: list[StateSpan]
    events: list[CommEvent]
    schema_version: str = SCHEMA_VERSION
    kind: Literal["single_trace"] = "single_trace"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "metadata": asdict(self.metadata),
            "metrics": asdict(self.metrics),
            "units": [asdict(u) for u in self.units],
            "spans": [
                {k: v for k, v in asdict(s).items() if v is not None}
                for s in self.spans
            ],
            "events": [asdict(e) for e in self.events],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass
class ComparisonPane:
    label: str
    trace: TraceDocument
    badge: str | None = None
    accent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "label": self.label,
            "trace": self.trace.to_dict(),
        }
        if self.badge is not None:
            out["badge"] = self.badge
        if self.accent is not None:
            out["accent"] = self.accent
        return out


@dataclass
class ComparisonDocument:
    title: str
    subtitle: str
    left_label: str = ""
    right_label: str = ""
    left: TraceDocument | None = None
    right: TraceDocument | None = None
    panes: list[ComparisonPane] = field(default_factory=list)
    default_scale_mode: Literal["wall_clock", "normalized"] = "wall_clock"
    schema_version: str = SCHEMA_VERSION
    kind: Literal["comparison_trace"] = "comparison_trace"

    def resolved_panes(self) -> list[ComparisonPane]:
        if self.panes:
            return self.panes
        if self.left is not None and self.right is not None:
            speedup = (
                round(
                    self.left.metadata.duration_ns
                    / max(1, self.right.metadata.duration_ns),
                    2,
                )
                if self.right.metadata.duration_ns > 0
                else 1.0
            )
            badge = f"{speedup}x Faster" if speedup >= 1.25 else None
            return [
                ComparisonPane(
                    label=self.left_label or self.left.metadata.title,
                    trace=self.left,
                    accent="#ff7b29",
                ),
                ComparisonPane(
                    label=self.right_label or self.right.metadata.title,
                    trace=self.right,
                    badge=badge,
                    accent="#10f5a0",
                ),
            ]
        raise ValueError("ComparisonDocument requires either panes or left+right traces")

    def to_dict(self) -> dict[str, Any]:
        panes_list = self.resolved_panes()
        first_dur = panes_list[0].trace.metadata.duration_ns
        last_dur = panes_list[-1].trace.metadata.duration_ns
        speedup = round(first_dur / max(1, last_dur), 2) if last_dur > 0 else 1.0
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "title": self.title,
            "subtitle": self.subtitle,
            "default_scale_mode": self.default_scale_mode,
            "speedup_factor": speedup,
            "panes": [p.to_dict() for p in panes_list],
        }
        if len(panes_list) == 2:
            result["left_label"] = panes_list[0].label
            result["right_label"] = panes_list[1].label
            result["left"] = panes_list[0].trace.to_dict()
            result["right"] = panes_list[1].trace.to_dict()
        return result

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def coalesce_unit_spans(
    spans: list[StateSpan],
    *,
    duration_ns: int,
    min_merge_gap_ns: int = 50_000,
) -> list[StateSpan]:
    """Clamp, make non-overlapping, and merge tiny adjacent same-state spans per unit."""
    by_unit: dict[str, list[StateSpan]] = {}
    for sp in spans:
        s = max(0, min(duration_ns, int(sp.start_ns)))
        e = max(s, min(duration_ns, int(sp.end_ns)))
        if e <= s:
            continue
        by_unit.setdefault(sp.unit_id, []).append(
            StateSpan(
                unit_id=sp.unit_id,
                state=sp.state,
                start_ns=s,
                end_ns=e,
                detail=sp.detail,
            )
        )

    out: list[StateSpan] = []
    for uid, unit_spans in by_unit.items():
        unit_spans.sort(key=lambda x: (x.start_ns, x.end_ns))
        cleaned: list[StateSpan] = []
        for sp in unit_spans:
            if not cleaned:
                cleaned.append(sp)
                continue
            prev = cleaned[-1]
            start = max(sp.start_ns, prev.end_ns)
            if sp.end_ns <= start:
                continue
            cur = StateSpan(
                unit_id=uid,
                state=sp.state,
                start_ns=start,
                end_ns=sp.end_ns,
                detail=sp.detail,
            )
            if (
                prev.state == cur.state
                and prev.detail == cur.detail
                and (cur.start_ns - prev.end_ns) <= min_merge_gap_ns
            ):
                prev.end_ns = cur.end_ns
            else:
                cleaned.append(cur)
        out.extend(cleaned)

    out.sort(key=lambda x: (x.start_ns, x.unit_id))
    return out


def compute_metrics(
    *,
    duration_ns: int,
    units: list[UnitRecord],
    spans: list[StateSpan],
    events: list[CommEvent],
) -> TraceMetrics:
    dur = max(1, duration_ns)
    totals: dict[str, int] = {st: 0 for st in VALID_STATES}
    cpu_sweep: list[tuple[int, int]] = []
    unit_sweep: list[tuple[int, int]] = []

    for u in units:
        if u.end_ns > u.start_ns:
            unit_sweep.append((u.start_ns, 1))
            unit_sweep.append((u.end_ns, -1))

    for sp in spans:
        delta = max(0, sp.end_ns - sp.start_ns)
        totals[sp.state] = totals.get(sp.state, 0) + delta
        if sp.state in ("running_cpu", "blocking_loop") and delta > 0:
            cpu_sweep.append((sp.start_ns, 1))
            cpu_sweep.append((sp.end_ns, -1))

    def _peak(points: list[tuple[int, int]]) -> int:
        # Process -1 before +1 on exact boundary ties so sequential handoffs do not double-count.
        points.sort(key=lambda item: (item[0], item[1]))
        cur = 0
        best = 0
        for _, d in points:
            cur += d
            if cur > best:
                best = cur
        return best

    peak_cpu = _peak(cpu_sweep)
    peak_units = _peak(unit_sweep)

    cpu_ns = totals["running_cpu"] + totals["blocking_loop"]
    gil_ns = totals["waiting_gil"]
    all_tracked_ns = sum(totals.values()) or 1

    msg_count = sum(1 for e in events if e.kind in ("queue_message", "task_result"))
    sync_count = sum(1 for e in events if e.kind in ("lock_handoff", "event_signal"))
    leaked_count = sum(1 for u in units if u.leaked)
    worker_count = sum(1 for u in units if u.kind != "main")

    return TraceMetrics(
        total_units=len(units),
        worker_units=worker_count,
        leaked_units=leaked_count,
        message_count=msg_count,
        sync_handoff_count=sync_count,
        duration_ms=round(dur / 1_000_000, 3),
        total_cpu_ms=round(cpu_ns / 1_000_000, 3),
        total_gil_wait_ms=round(gil_ns / 1_000_000, 3),
        total_io_wait_ms=round(totals["waiting_io"] / 1_000_000, 3),
        total_sync_wait_ms=round(totals["waiting_sync"] / 1_000_000, 3),
        total_await_wait_ms=round(totals["suspended_await"] / 1_000_000, 3),
        total_blocking_loop_ms=round(totals["blocking_loop"] / 1_000_000, 3),
        effective_parallelism=round(cpu_ns / dur, 3),
        peak_parallel_cpu_units=peak_cpu,
        peak_concurrent_units=peak_units,
        gil_wait_pct=round(100.0 * gil_ns / all_tracked_ns, 2),
        cpu_active_pct=round(100.0 * cpu_ns / all_tracked_ns, 2),
    )


def validate_trace_dict(data: dict[str, Any]) -> None:
    """Validate single trace or comparison trace JSON dictionary; raises ValueError on violation."""
    if not isinstance(data, dict):
        raise ValueError("Trace document must be a JSON object")
    kind = data.get("kind", "single_trace")
    if kind == "comparison_trace":
        if "panes" in data and isinstance(data["panes"], list) and len(data["panes"]) >= 2:
            for pane in data["panes"]:
                if "trace" not in pane or "label" not in pane:
                    raise ValueError("Each comparison pane must have 'label' and 'trace'")
                validate_trace_dict(pane["trace"])
            return
        if "left" not in data or "right" not in data:
            raise ValueError("Comparison trace must include 'panes' or 'left' and 'right' traces")
        validate_trace_dict(data["left"])
        validate_trace_dict(data["right"])
        return

    for req in ("schema_version", "metadata", "metrics", "units", "spans", "events"):
        if req not in data:
            raise ValueError(f"Missing required top-level field: {req}")

    meta = data["metadata"]
    dur = meta.get("duration_ns")
    if not isinstance(dur, int) or dur <= 0:
        raise ValueError(f"Invalid metadata.duration_ns: {dur}")
    if meta.get("model") not in ("asyncio", "threading"):
        raise ValueError(f"Invalid metadata.model: {meta.get('model')}")
    if not isinstance(meta.get("gil_enabled"), bool):
        raise ValueError("metadata.gil_enabled must be boolean")

    unit_ids: set[str] = set()
    for u in data["units"]:
        uid = u["id"]
        if uid in unit_ids:
            raise ValueError(f"Duplicate unit id: {uid}")
        unit_ids.add(uid)
        if not (0 <= u["start_ns"] <= u["end_ns"] <= dur):
            raise ValueError(
                f"Unit {uid} timestamps out of bounds: {u['start_ns']}..{u['end_ns']} (dur={dur})"
            )

    if "main" not in unit_ids:
        raise ValueError("Trace must contain a 'main' unit")

    last_end_by_unit: dict[str, int] = {}
    for sp in data["spans"]:
        uid = sp["unit_id"]
        if uid not in unit_ids:
            raise ValueError(f"Span references unknown unit_id: {uid}")
        if sp["state"] not in VALID_STATES:
            raise ValueError(f"Invalid span state: {sp['state']}")
        s, e = sp["start_ns"], sp["end_ns"]
        if not (0 <= s <= e <= dur):
            raise ValueError(f"Span out of bounds for {uid}: {s}..{e} (dur={dur})")
        prev_end = last_end_by_unit.get(uid, 0)
        if s < prev_end:
            raise ValueError(
                f"Overlapping span for {uid}: start_ns={s} < previous end_ns={prev_end}"
            )
        last_end_by_unit[uid] = e

    for ev in data["events"]:
        if ev["kind"] not in VALID_EVENT_KINDS:
            raise ValueError(f"Invalid event kind: {ev['kind']}")
        if ev["from_unit"] not in unit_ids or ev["to_unit"] not in unit_ids:
            raise ValueError(
                f"Event {ev['id']} references unknown unit: {ev['from_unit']} -> {ev['to_unit']}"
            )
        if not (0 <= ev["send_ns"] <= ev["recv_ns"] <= dur):
            raise ValueError(
                f"Event {ev['id']} timestamps out of bounds: {ev['send_ns']}..{ev['recv_ns']} (dur={dur})"
            )
