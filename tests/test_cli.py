"""CLI, HTTP server, and built curriculum invariant tests."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from pyconvis.cli import main as cli_main
from pyconvis.runner import find_freethreaded_python
from pyconvis.schema import validate_trace_dict
from pyconvis.server import create_http_server


def test_cli_trace_asyncio_and_threading(tmp_path: Path) -> None:
    async_script = tmp_path / "sample_async.py"
    async_script.write_text(
        "import asyncio\n"
        "async def main():\n"
        "    q = asyncio.Queue()\n"
        "    async def prod():\n"
        "        await asyncio.sleep(0.01)\n"
        "        await q.put('hi')\n"
        "    t = asyncio.create_task(prod())\n"
        "    await q.get()\n"
        "    await t\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )
    out_json = tmp_path / "async_trace.json"
    rc = cli_main(["trace", str(async_script), "-o", str(out_json)])
    assert rc == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    validate_trace_dict(data)
    assert data["metadata"]["model"] == "asyncio"
    assert data["metrics"]["message_count"] == 1


def test_cli_compare_gil_and_missing_binary_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "tiny_threads.py"
    script.write_text(
        "import threading\n"
        "def work():\n"
        "    x = 0\n"
        "    for i in range(200):\n"
        "        x = (x ^ (i & 0xff)) & 0xff\n"
        "ts = [threading.Thread(target=work) for _ in range(2)]\n"
        "for t in ts: t.start()\n"
        "for t in ts: t.join()\n",
        encoding="utf-8",
    )

    # 1. Verify clear diagnostic when pointing --python at a non-existent / non-freethreaded binary
    bad_out = tmp_path / "bad.json"
    rc_bad = cli_main(
        [
            "compare-gil",
            str(script),
            "--python",
            "/usr/bin/true",
            "-o",
            str(bad_out),
        ]
    )
    assert rc_bad != 0
    captured = capsys.readouterr()
    assert "Error:" in captured.err

    # 2. Verify real comparison when free-threaded Python is available
    ft = find_freethreaded_python()
    if ft is not None:
        good_out = tmp_path / "compare.json"
        rc_ok = cli_main(["compare-gil", str(script), "-o", str(good_out)])
        assert rc_ok == 0
        comp = json.loads(good_out.read_text(encoding="utf-8"))
        validate_trace_dict(comp)
        assert comp["kind"] == "comparison_trace"
        assert comp["left"]["metadata"]["gil_enabled"] is True
        assert comp["right"]["metadata"]["gil_enabled"] is False


def test_http_server_serves_index_and_custom_trace(tmp_path: Path) -> None:
    trace_file = Path(__file__).resolve().parent.parent / "web" / "public" / "traces" / "02_recurrent_timers.json"
    sample_trace = json.loads(trace_file.read_text(encoding="utf-8"))

    httpd = create_http_server(host="127.0.0.1", port=0, custom_trace=sample_trace)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            html = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "Visualizing Concurrency" in html

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/custom-trace"
        ) as resp2:
            api_data = json.loads(resp2.read().decode("utf-8"))
            assert resp2.status == 200
            assert api_data["metadata"]["model"] == "asyncio"

        live_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/run-live",
            data=json.dumps(
                {
                    "code": "import asyncio\nasync def main():\n    await asyncio.sleep(0.01)\nasyncio.run(main())\n",
                    "run_mode": "auto",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(live_req) as resp3:
            live_doc = json.loads(resp3.read().decode("utf-8"))
            assert resp3.status == 200
            validate_trace_dict(live_doc)
            assert live_doc["metadata"]["model"] == "asyncio"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_all_shipped_curriculum_traces_are_valid() -> None:
    traces_dir = Path(__file__).resolve().parent.parent / "web" / "public" / "traces"
    manifest_path = traces_dir / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["lesson_count"] == 7

    for lesson in manifest["lessons"]:
        assert len(lesson["legend_keys"]) >= 2
        assert len(lesson["takeaways"]) >= 2
        tfile = traces_dir / lesson["trace_file"]
        assert tfile.is_file(), f"Missing trace file: {tfile}"
        doc = json.loads(tfile.read_text(encoding="utf-8"))
        validate_trace_dict(doc)

    # Lesson 07 must contain all 3 synchronized asyncio panes (Trap, Native Async, to_thread Offload)
    three_way = json.loads((traces_dir / "07_blocking_3way.json").read_text(encoding="utf-8"))
    assert len(three_way["panes"]) == 3
    pane3_units = three_way["panes"][2]["trace"]["units"]
    assert any(u["kind"] == "thread" and "slow_task" in u["name"] for u in pane3_units)
