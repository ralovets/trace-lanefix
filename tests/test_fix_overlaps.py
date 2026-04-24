import json
from collections import defaultdict

import pytest

from trace_lanefix import fix_overlaps


def _run_fix(trace, tmp_path, **kwargs):
    in_path = tmp_path / "in.json"
    out_path = tmp_path / "out.json"
    in_path.write_text(json.dumps(trace))
    fix_overlaps(str(in_path), str(out_path), **kwargs)
    return json.loads(out_path.read_text())["traceEvents"]


def _assert_no_crossings(events):
    slices = [e for e in events if e.get("ph") == "X" and "dur" in e]
    by_track = defaultdict(list)
    for e in slices:
        by_track[(e.get("pid"), e.get("tid"))].append(e)

    for group in by_track.values():
        group.sort(key=lambda e: (e["ts"], -e["dur"]))
        stack = []
        for e in group:
            start = e["ts"]
            end = start + e["dur"]
            while stack and stack[-1][0] <= start:
                stack.pop()
            if stack:
                top_end, top_start = stack[-1]
                assert top_start <= start and end <= top_end
            stack.append((end, start))


def test_start_snap_rechecks_outer_parent(tmp_path):
    trace = {
        "traceEvents": [
            {"ph": "X", "cat": "python_function", "name": "outer", "pid": 1, "tid": 1,
             "ts": 0.0, "dur": 3.405},
            {"ph": "X", "cat": "python_function", "name": "inner", "pid": 1, "tid": 1,
             "ts": 0.170, "dur": 3.205},
            {"ph": "X", "cat": "user_annotation", "name": "layer0_forward", "pid": 1, "tid": 1,
             "ts": 1.122, "dur": 10.0},
        ]
    }

    events = _run_fix(trace, tmp_path)
    _assert_no_crossings(events)

    layer = next(e for e in events if e.get("name") == "layer0_forward")
    assert layer["tid"] == 1
    assert layer["ts"] == pytest.approx(3.405)
    assert layer["dur"] == pytest.approx(7.717)


def test_flow_endpoint_tracks_start_snap(tmp_path):
    trace = {
        "traceEvents": [
            {"ph": "X", "cat": "cuda_runtime", "name": "cudaLaunchKernel", "pid": 11, "tid": 11,
             "ts": 10.0, "dur": 2.0},
            {"ph": "s", "id": 1, "pid": 11, "tid": 11, "ts": 10.0, "cat": "ac2g", "name": "ac2g"},
            {"ph": "X", "cat": "kernel", "name": "kernel_a", "pid": 0, "tid": 7,
             "ts": 100.0, "dur": 50.0},
            {"ph": "X", "cat": "kernel", "name": "kernel_b", "pid": 0, "tid": 7,
             "ts": 149.7, "dur": 10.0},
            {"ph": "f", "id": 1, "pid": 0, "tid": 7, "ts": 149.7, "cat": "ac2g", "name": "ac2g", "bp": "e"},
        ]
    }

    events = _run_fix(trace, tmp_path)
    _assert_no_crossings(events)

    kernel_b = next(e for e in events if e.get("name") == "kernel_b")
    flow_f = next(e for e in events if e.get("ph") == "f" and e.get("id") == 1)

    assert kernel_b["tid"] == 7
    assert kernel_b["ts"] == pytest.approx(150.0)
    assert flow_f["tid"] == 7
    assert flow_f["ts"] == pytest.approx(150.0)


def test_snap_threshold_can_force_synthetic_lane(tmp_path):
    trace = {
        "traceEvents": [
            {"ph": "X", "cat": "kernel", "name": "kernel_a", "pid": 0, "tid": 7,
             "ts": 100.0, "dur": 50.0},
            {"ph": "X", "cat": "kernel", "name": "kernel_b", "pid": 0, "tid": 7,
             "ts": 149.7, "dur": 10.0},
        ]
    }

    events = _run_fix(trace, tmp_path, snap_threshold_us=0.1)
    _assert_no_crossings(events)

    kernel_b = next(e for e in events if e.get("name") == "kernel_b")
    assert kernel_b["tid"] == 7001
    assert kernel_b["ts"] == pytest.approx(149.7)


def test_child_before_start_snapped_parent_is_rechecked(tmp_path):
    trace = {
        "traceEvents": [
            {"ph": "X", "cat": "cpu", "name": "previous", "pid": 1, "tid": 1,
             "ts": 0.0, "dur": 10.0},
            {"ph": "X", "cat": "cpu", "name": "parent", "pid": 1, "tid": 1,
             "ts": 9.9, "dur": 10.0},
            {"ph": "X", "cat": "cpu", "name": "child", "pid": 1, "tid": 1,
             "ts": 9.95, "dur": 1.0},
        ]
    }

    events = _run_fix(trace, tmp_path)
    _assert_no_crossings(events)

    parent = next(e for e in events if e.get("name") == "parent")
    child = next(e for e in events if e.get("name") == "child")
    assert parent["tid"] == 1
    assert parent["ts"] == pytest.approx(10.0)
    assert child["tid"] == 1
    assert child["ts"] == pytest.approx(10.0)
    assert child["dur"] == pytest.approx(0.95)


def test_synthetic_lane_tid_avoids_real_track_collision(tmp_path):
    trace = {
        "traceEvents": [
            {"ph": "X", "cat": "cpu", "name": "real_1001", "pid": 1, "tid": 1001,
             "ts": 100.0, "dur": 1.0},
            {"ph": "X", "cat": "cpu", "name": "a", "pid": 1, "tid": 1,
             "ts": 0.0, "dur": 10.0},
            {"ph": "X", "cat": "cpu", "name": "b", "pid": 1, "tid": 1,
             "ts": 5.0, "dur": 10.0},
        ]
    }

    events = _run_fix(trace, tmp_path)
    _assert_no_crossings(events)

    moved = next(e for e in events if e.get("name") == "b")
    real = next(e for e in events if e.get("name") == "real_1001")
    lane_name = next(
        e for e in events
        if e.get("name") == "thread_name" and e.get("args", {}).get("name") == "tid 1 lane 1"
    )
    assert real["tid"] == 1001
    assert moved["tid"] == "1 lane 1"
    assert lane_name["tid"] == "1 lane 1"


def test_string_tid_can_move_to_synthetic_lane(tmp_path):
    trace = {
        "traceEvents": [
            {"ph": "X", "cat": "cpu", "name": "a", "pid": 1, "tid": "worker",
             "ts": 0.0, "dur": 10.0},
            {"ph": "X", "cat": "cpu", "name": "b", "pid": 1, "tid": "worker",
             "ts": 5.0, "dur": 10.0},
        ]
    }

    events = _run_fix(trace, tmp_path)
    _assert_no_crossings(events)

    moved = next(e for e in events if e.get("name") == "b")
    assert moved["tid"] == "worker lane 1"


def test_output_json_is_pretty_printed(tmp_path):
    trace = {
        "schemaVersion": 1,
        "traceEvents": [
            {"ph": "X", "cat": "cpu", "name": "a", "pid": 1, "tid": 1,
             "ts": 0.0, "dur": 1.0},
        ],
    }
    in_path = tmp_path / "in.json"
    out_path = tmp_path / "out.json"
    in_path.write_text(json.dumps(trace))

    fix_overlaps(str(in_path), str(out_path))

    text = out_path.read_text()
    assert text.endswith("\n")
    assert "\n  \"schemaVersion\": 1,\n" in text
    assert "\n    {\n" in text


def test_reports_grouped_kernel_summary_sorted_by_total_shift(tmp_path, capsys):
    trace = {
        "traceEvents": [
            {"ph": "X", "cat": "kernel", "name": "kernel_a", "pid": 0, "tid": 7,
             "ts": 100.0, "dur": 50.0},
            {"ph": "X", "cat": "kernel", "name": "kernel_c", "pid": 0, "tid": 7,
             "ts": 120.0, "dur": 40.0},
            {"ph": "X", "cat": "kernel", "name": "kernel_b", "pid": 0, "tid": 7,
             "ts": 149.7, "dur": 10.0},
            {"ph": "X", "cat": "kernel", "name": "kernel_b", "pid": 0, "tid": 7,
             "ts": 159.6, "dur": 10.0},
        ]
    }

    events = _run_fix(trace, tmp_path)
    _assert_no_crossings(events)

    out = capsys.readouterr().out
    assert "updated 3 slice(s)" in out
    assert "kernel timing summary (3 run(s) across 2 kernel name(s)):" in out
    assert "  kernel_b: runs=2, total delayed=400ns, total trimmed=0.000us, total boundary shift=400ns, moved=0, snapped=2" in out
    assert "  kernel_c: runs=1, total delayed=0.000us, total trimmed=0.000us, total boundary shift=0.000us, moved=1, snapped=0" in out
    assert "moved slices" not in out
    assert "snapped slices" not in out
    assert out.index("  kernel_b:") < out.index("  kernel_c:")
