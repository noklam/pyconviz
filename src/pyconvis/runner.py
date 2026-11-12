"""Script mode detection, free-threaded Python discovery, and subprocess trace runner."""

from __future__ import annotations

import ast
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from pyconvis.asyncio_tracer import trace_asyncio_script
from pyconvis.schema import (
    CommEvent,
    ComparisonDocument,
    RuntimeMetadata,
    StateSpan,
    TraceDocument,
    TraceMetrics,
    UnitRecord,
    validate_trace_dict,
)
from pyconvis.threading_tracer import trace_threading_script


def detect_script_mode(script_path: str | Path) -> Literal["asyncio", "threading"]:
    """Inspect AST of a Python script to determine whether it runs asyncio or threading."""
    source = Path(script_path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "threading"

    has_async_def = False
    imports_asyncio = False
    calls_asyncio_run = False

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            has_async_def = True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "asyncio" or alias.name.startswith("asyncio."):
                    imports_asyncio = True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "asyncio" or node.module.startswith("asyncio.")
            ):
                imports_asyncio = True
        elif isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            ):
                calls_asyncio_run = True

    if calls_asyncio_run or (has_async_def and imports_asyncio):
        return "asyncio"
    return "threading"


def _is_freethreaded_binary(python_bin: str) -> bool:
    try:
        proc = subprocess.run(
            [
                python_bin,
                "-c",
                "import sys; fn=getattr(sys,'_is_gil_enabled',None); print('OK' if callable(fn) and not fn() else 'NO')",
            ],
            env={**os.environ, "PYTHON_GIL": "0"},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "OK"
    except Exception:
        return False


def find_freethreaded_python() -> str | None:
    """Locate a free-threaded CPython (3.13t / 3.14t+) executable capable of PYTHON_GIL=0."""
    candidates: list[str] = []

    env_override = os.environ.get("PYCONVIS_FREETHREADED_PYTHON")
    if env_override:
        candidates.append(env_override)

    candidates.append(sys.executable)

    for name in ("python3.14t", "python3.13t", "python3.15t"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    uv_bin = shutil.which("uv")
    if uv_bin:
        for spec in ("3.14t", "3.13t"):
            try:
                proc = subprocess.run(
                    [uv_bin, "python", "find", spec],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    candidates.append(proc.stdout.strip())
            except Exception:
                pass

    uv_share = Path.home() / ".local" / "share" / "uv" / "python"
    if uv_share.exists():
        matches = sorted(
            glob.glob(str(uv_share / "*freethreaded*" / "bin" / "python3*t")),
            reverse=True,
        )
        candidates.extend(matches)

    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            if _is_freethreaded_binary(cand):
                return cand

    return None


def trace_dict_to_document(data: dict[str, Any]) -> TraceDocument:
    validate_trace_dict(data)
    meta = RuntimeMetadata(**data["metadata"])
    metrics = TraceMetrics(**data["metrics"])
    units = [UnitRecord(**u) for u in data["units"]]
    spans = [StateSpan(**s) for s in data["spans"]]
    events = [CommEvent(**e) for e in data["events"]]
    return TraceDocument(
        metadata=meta,
        metrics=metrics,
        units=units,
        spans=spans,
        events=events,
        schema_version=data.get("schema_version", "1.0"),
    )


def trace_script_in_process(
    script_path: str | Path,
    *,
    mode: Literal["auto", "asyncio", "threading"] = "auto",
    title: str | None = None,
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    switch_interval_s: float = 0.002,
    blocking_threshold_ms: float = 15.0,
    record_gil_handoffs: bool = False,
) -> TraceDocument:
    resolved_mode = detect_script_mode(script_path) if mode == "auto" else mode
    if resolved_mode == "asyncio":
        return trace_asyncio_script(
            script_path,
            title=title,
            layout_hint=layout_hint,
            blocking_threshold_ms=blocking_threshold_ms,
        )
    return trace_threading_script(
        script_path,
        title=title,
        layout_hint=layout_hint,
        switch_interval_s=switch_interval_s,
        record_gil_handoffs=record_gil_handoffs,
    )


def trace_script_subprocess(
    script_path: str | Path,
    *,
    mode: Literal["auto", "asyncio", "threading"] = "auto",
    gil: Literal[0, 1] | None = None,
    python_executable: str | None = None,
    title: str | None = None,
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    switch_interval_s: float = 0.002,
    blocking_threshold_ms: float = 15.0,
    record_gil_handoffs: bool = False,
) -> TraceDocument:
    """Run script tracing in a clean subprocess (optionally controlling PYTHON_GIL=0|1)."""
    path = Path(script_path).resolve()
    exe = python_executable
    if exe is None:
        if gil == 0:
            exe = find_freethreaded_python()
            if exe is None:
                raise RuntimeError(
                    "Free-threaded Python (python3.14t / python3.13t) is required for PYTHON_GIL=0. "
                    "Install it with `uv python install 3.14t` or set PYCONVIS_FREETHREADED_PYTHON."
                )
        else:
            exe = sys.executable

    src_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_root}{os.pathsep}{existing_pp}" if existing_pp else src_root
    if gil is not None:
        env["PYTHON_GIL"] = str(gil)

    helper_code = (
        "import json, sys\n"
        "cfg = json.loads(sys.argv[1])\n"
        "sys.path.insert(0, cfg['src_root'])\n"
        "from pyconvis.runner import trace_script_in_process\n"
        "doc = trace_script_in_process(\n"
        "    cfg['script_path'],\n"
        "    mode=cfg['mode'],\n"
        "    title=cfg['title'],\n"
        "    layout_hint=cfg['layout_hint'],\n"
        "    switch_interval_s=cfg['switch_interval_s'],\n"
        "    blocking_threshold_ms=cfg['blocking_threshold_ms'],\n"
        "    record_gil_handoffs=cfg.get('record_gil_handoffs', False),\n"
        ")\n"
        # Use sentinel marker so user print() output to stdout never corrupts JSON parsing!
        "sys.stdout.write('\\n__PYCONVIS_JSON_START__\\n' + doc.to_json(indent=None) + '\\n__PYCONVIS_JSON_END__\\n')\n"
        "sys.stdout.flush()\n"
        "import os; os._exit(0)\n"
    )
    payload = json.dumps(
        {
            "src_root": src_root,
            "script_path": str(path),
            "mode": mode,
            "title": title or path.stem,
            "layout_hint": layout_hint,
            "switch_interval_s": switch_interval_s,
            "blocking_threshold_ms": blocking_threshold_ms,
            "record_gil_handoffs": record_gil_handoffs,
        }
    )

    proc = subprocess.run(
        [exe, "-c", helper_code, payload],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    out = proc.stdout
    start_tag = "__PYCONVIS_JSON_START__"
    end_tag = "__PYCONVIS_JSON_END__"
    if proc.returncode != 0 or start_tag not in out or end_tag not in out:
        raise RuntimeError(
            f"Subprocess trace failed for {path.name} (exit={proc.returncode}):\n"
            f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
        )

    raw_json = out.split(start_tag, 1)[1].split(end_tag, 1)[0].strip()
    return trace_dict_to_document(json.loads(raw_json))


def compare_gil_subprocess(
    script_path: str | Path,
    *,
    python_executable: str | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    layout_hint: Literal["radial", "ring", "pipeline", "tree"] = "radial",
    switch_interval_s: float = 0.002,
) -> ComparisonDocument:
    """Run the exact same script with PYTHON_GIL=1 and PYTHON_GIL=0 on free-threaded Python."""
    path = Path(script_path).resolve()
    exe = python_executable or find_freethreaded_python()
    if exe is None:
        raise RuntimeError(
            "Free-threaded Python (e.g. python3.14t) not found. "
            "Install it via `uv python install 3.14t` or set PYCONVIS_FREETHREADED_PYTHON."
        )

    base_title = title or f"{path.stem}: GIL vs Free-Threaded"
    with_gil = trace_script_subprocess(
        path,
        mode="threading",
        gil=1,
        python_executable=exe,
        title=f"{path.stem} (PYTHON_GIL=1)",
        layout_hint=layout_hint,
        switch_interval_s=switch_interval_s,
    )
    no_gil = trace_script_subprocess(
        path,
        mode="threading",
        gil=0,
        python_executable=exe,
        title=f"{path.stem} (PYTHON_GIL=0)",
        layout_hint=layout_hint,
        switch_interval_s=switch_interval_s,
    )

    if not with_gil.metadata.gil_enabled:
        raise RuntimeError("Expected gil_enabled=True under PYTHON_GIL=1")
    if no_gil.metadata.gil_enabled:
        raise RuntimeError("Expected gil_enabled=False under PYTHON_GIL=0")

    return ComparisonDocument(
        title=base_title,
        subtitle=subtitle
        or "Identical Python code executed on CPython free-threading runtime with PYTHON_GIL=1 vs PYTHON_GIL=0",
        left_label="Standard GIL (PYTHON_GIL=1)",
        right_label="Free-Threaded No-GIL (PYTHON_GIL=0)",
        left=with_gil,
        right=no_gil,
        default_scale_mode="wall_clock",
    )
