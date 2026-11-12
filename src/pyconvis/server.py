"""Local HTTP server hosting the 3D WebGL visualizer, custom trace API, and live local code runner."""

from __future__ import annotations

import functools
import http.server
import json
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

from pyconvis.runner import (
    compare_gil_subprocess,
    find_freethreaded_python,
    trace_script_subprocess,
)
from pyconvis.schema import validate_trace_dict


def resolve_static_root() -> Path:
    pkg_static = Path(__file__).resolve().parent / "static"
    if (pkg_static / "index.html").is_file():
        return pkg_static

    repo_dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if (repo_dist / "index.html").is_file():
        return repo_dist

    raise FileNotFoundError(
        "Static web assets not found in src/pyconvis/static or web/dist. "
        "Run `cd web && npm run build` first."
    )


def create_http_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    custom_trace: dict[str, Any] | None = None,
    static_root: Path | None = None,
) -> http.server.ThreadingHTTPServer:
    if custom_trace is not None:
        validate_trace_dict(custom_trace)

    root = static_root or resolve_static_root()
    encoded_trace = (
        json.dumps(custom_trace).encode("utf-8") if custom_trace is not None else None
    )
    ft_python = find_freethreaded_python()

    class VisualizerHandler(http.server.SimpleHTTPRequestHandler):
        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            clean_path = self.path.split("?", 1)[0]
            if clean_path == "/api/capabilities":
                self._send_json(
                    200,
                    {
                        "live_runner": True,
                        "freethreaded_available": ft_python is not None,
                    },
                )
                return
            if clean_path == "/api/custom-trace":
                if encoded_trace is None:
                    self._send_json(404, {"error": "no custom trace loaded"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded_trace)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded_trace)
                return
            super().do_GET()

        def do_POST(self) -> None:
            clean_path = self.path.split("?", 1)[0]
            if clean_path != "/api/run-live":
                self._send_json(404, {"error": "unknown endpoint"})
                return

            try:
                content_len = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_len).decode("utf-8")
                req = json.loads(raw_body)
                code = str(req.get("code", ""))
                run_mode = str(req.get("run_mode", "auto"))
                if not code.strip():
                    self._send_json(400, {"error": "Python code cannot be empty."})
                    return

                with tempfile.TemporaryDirectory() as td:
                    script_file = Path(td) / "live_snippet.py"
                    script_file.write_text(code, encoding="utf-8")

                    if run_mode == "compare_gil":
                        comp = compare_gil_subprocess(
                            script_file,
                            python_executable=ft_python,
                            title="Live Run: GIL ON vs GIL OFF",
                        )
                        self._send_json(200, comp.to_dict())
                        return

                    gil_flag: int | None = None
                    py_exe: str | None = None
                    mode_arg = "auto"
                    if run_mode == "threading_gil1":
                        mode_arg = "threading"
                        gil_flag = 1
                        py_exe = ft_python
                    elif run_mode == "threading_gil0":
                        mode_arg = "threading"
                        gil_flag = 0
                        py_exe = ft_python
                    elif run_mode in ("asyncio", "threading"):
                        mode_arg = run_mode

                    doc = trace_script_subprocess(
                        script_file,
                        mode=mode_arg,  # type: ignore[arg-type]
                        gil=gil_flag,  # type: ignore[arg-type]
                        python_executable=py_exe,
                        title="Live Python Run",
                    )
                    self._send_json(200, doc.to_dict())
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            pass

    handler_cls = functools.partial(VisualizerHandler, directory=str(root))
    return http.server.ThreadingHTTPServer((host, port), handler_cls)


def serve_visualizer(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    custom_trace: dict[str, Any] | None = None,
    open_browser: bool = False,
) -> None:
    httpd = create_http_server(host=host, port=port, custom_trace=custom_trace)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}"
    print(f"pyconvis 3D Spacetime Lab running at {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping pyconvis server.")
    finally:
        httpd.server_close()
