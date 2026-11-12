"""Command-line interface for pyconvis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pyconvis.curriculum import build_all_examples
from pyconvis.runner import compare_gil_subprocess, trace_script_subprocess
from pyconvis.schema import validate_trace_dict
from pyconvis.server import serve_visualizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyconvis",
        description=(
            "Trace and visualize Python concurrency & parallelism in 3D WebGL "
            "(asyncio, GIL threading, and Free-Threaded Python 3.14t)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. pyconvis trace
    p_trace = sub.add_parser(
        "trace",
        help="Execute a Python script under runtime tracing and export JSON or serve in 3D",
    )
    p_trace.add_argument("script", help="Path to Python script (.py)")
    p_trace.add_argument("-o", "--output", help="Output JSON file path")
    p_trace.add_argument(
        "--mode",
        choices=["auto", "asyncio", "threading"],
        default="auto",
        help="Concurrency model to trace (default: auto-detect from AST)",
    )
    p_trace.add_argument(
        "--gil",
        type=int,
        choices=[0, 1],
        default=None,
        help="Explicitly run under PYTHON_GIL=0 or PYTHON_GIL=1",
    )
    p_trace.add_argument(
        "--python",
        dest="python_exe",
        default=None,
        help="Optional Python interpreter path",
    )
    p_trace.add_argument(
        "--layout",
        choices=["radial", "ring", "pipeline", "tree"],
        default="radial",
        help="3D spatial layout hint",
    )
    p_trace.add_argument(
        "--title", default=None, help="Display title for the trace"
    )
    p_trace.add_argument(
        "--gil-handoffs",
        action="store_true",
        help="Emit explicit gil_handoff arrows across threads when GIL is enabled",
    )
    p_trace.add_argument(
        "--serve",
        action="store_true",
        help="Launch local 3D visualizer server after capturing trace",
    )
    p_trace.add_argument("--host", default="127.0.0.1")
    p_trace.add_argument("--port", type=int, default=8765)
    p_trace.add_argument("--open", action="store_true", help="Open browser automatically")

    # 2. pyconvis compare-gil
    p_comp = sub.add_parser(
        "compare-gil",
        help="Run a threaded Python script twice (PYTHON_GIL=1 vs PYTHON_GIL=0) for side-by-side 3D comparison",
    )
    p_comp.add_argument("script", help="Path to threaded Python script (.py)")
    p_comp.add_argument("-o", "--output", help="Output comparison JSON path")
    p_comp.add_argument(
        "--python",
        dest="python_exe",
        default=None,
        help="Path to free-threaded Python executable (e.g. python3.14t)",
    )
    p_comp.add_argument(
        "--layout",
        choices=["radial", "ring", "pipeline", "tree"],
        default="radial",
    )
    p_comp.add_argument("--title", default=None)
    p_comp.add_argument("--serve", action="store_true")
    p_comp.add_argument("--host", default="127.0.0.1")
    p_comp.add_argument("--port", type=int, default=8765)
    p_comp.add_argument("--open", action="store_true")

    # 3. pyconvis serve
    p_serve = sub.add_parser(
        "serve",
        help="Start local HTTP server hosting the 14-lesson 3D lab (or a specific trace JSON)",
    )
    p_serve.add_argument(
        "trace_json",
        nargs="?",
        default=None,
        help="Optional path to trace.json or comparison.json to preload",
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--open", action="store_true")

    # 4. pyconvis build-examples
    p_build = sub.add_parser(
        "build-examples",
        help="Execute all built-in educational examples and generate web trace assets",
    )
    p_build.add_argument("--examples-dir", default="examples")
    p_build.add_argument("--output-dir", default="web/public/traces")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "trace":
            script_path = Path(args.script)
            if not script_path.is_file():
                print(f"Error: Script not found: {script_path}", file=sys.stderr)
                return 2
            doc = trace_script_subprocess(
                script_path,
                mode=args.mode,
                gil=args.gil,
                python_executable=args.python_exe,
                title=args.title,
                layout_hint=args.layout,
                record_gil_handoffs=args.gil_handoffs,
            )
            payload = doc.to_dict()
            if args.output:
                out_path = Path(args.output)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                print(
                    f"Wrote {doc.metadata.model} trace ({doc.metrics.duration_ms:.1f} ms, "
                    f"{doc.metrics.total_units} units, {doc.metrics.message_count} msgs) -> {out_path}"
                )
            if args.serve or not args.output:
                serve_visualizer(
                    host=args.host,
                    port=args.port,
                    custom_trace=payload,
                    open_browser=args.open,
                )
            return 0

        if args.command == "compare-gil":
            script_path = Path(args.script)
            if not script_path.is_file():
                print(f"Error: Script not found: {script_path}", file=sys.stderr)
                return 2
            comp = compare_gil_subprocess(
                script_path,
                python_executable=args.python_exe,
                title=args.title,
                layout_hint=args.layout,
            )
            payload = comp.to_dict()
            if args.output:
                out_path = Path(args.output)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                print(
                    f"Wrote GIL comparison: GIL=1 {comp.left.metrics.duration_ms:.1f} ms vs "
                    f"GIL=0 {comp.right.metrics.duration_ms:.1f} ms "
                    f"({payload['speedup_factor']}x) -> {out_path}"
                )
            if args.serve or not args.output:
                serve_visualizer(
                    host=args.host,
                    port=args.port,
                    custom_trace=payload,
                    open_browser=args.open,
                )
            return 0

        if args.command == "serve":
            custom_payload = None
            if args.trace_json:
                tpath = Path(args.trace_json)
                if not tpath.is_file():
                    print(f"Error: Trace JSON not found: {tpath}", file=sys.stderr)
                    return 2
                custom_payload = json.loads(tpath.read_text(encoding="utf-8"))
                validate_trace_dict(custom_payload)
            serve_visualizer(
                host=args.host,
                port=args.port,
                custom_trace=custom_payload,
                open_browser=args.open,
            )
            return 0

        if args.command == "build-examples":
            manifest = build_all_examples(
                examples_dir=Path(args.examples_dir),
                output_dir=Path(args.output_dir),
            )
            print(
                f"Built {manifest['lesson_count']} lessons into {args.output_dir}"
            )
            return 0

    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
