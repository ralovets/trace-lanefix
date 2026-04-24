"""Rewrite Chrome/Kineto traces so Perfetto can render crossing slices."""

import json
from collections import defaultdict

SNAP_THRESHOLD_US = 5.0


def _format_us(value: float) -> str:
    if 0 < abs(value) < 1.0:
        return f"{value * 1000.0:.1f}".rstrip("0").rstrip(".") + "ns"
    return f"{value:.3f}us"


def _describe_change(change: dict) -> str:
    start_delta = change["new_start"] - change["orig_start"]
    end_delta = change["new_end"] - change["orig_end"]
    parts = []

    if change["orig_tid"] != change["new_tid"]:
        parts.append(f"moved to lane {change['lane_idx']} (tid {change['orig_tid']} -> {change['new_tid']})")

    if start_delta == end_delta and start_delta != 0:
        parts.append(f"shifted {'later' if start_delta > 0 else 'earlier'} by {_format_us(abs(start_delta))}")
    else:
        if start_delta > 0:
            parts.append(f"start delayed by {_format_us(start_delta)}")
        elif start_delta < 0:
            parts.append(f"start moved earlier by {_format_us(-start_delta)}")

        if end_delta > 0:
            parts.append(f"end extended by {_format_us(end_delta)}")
        elif end_delta < 0:
            parts.append(f"end trimmed by {_format_us(-end_delta)}")

    return ", ".join(parts) or "timing unchanged"


def _change_shift_stats(change: dict) -> dict:
    start_delta = change["new_start"] - change["orig_start"]
    end_delta = change["new_end"] - change["orig_end"]
    return {
        "start_delayed": max(0.0, start_delta),
        "start_earlier": max(0.0, -start_delta),
        "end_extended": max(0.0, end_delta),
        "end_trimmed": max(0.0, -end_delta),
        "boundary_shift": abs(start_delta) + abs(end_delta),
    }


def _summarize_changes_by_name(changes: list[dict]) -> list[dict]:
    summaries = {}
    for change in changes:
        summary = summaries.setdefault(
            change["name"],
            {
                "name": change["name"],
                "runs": 0,
                "start_delayed": 0.0,
                "start_earlier": 0.0,
                "end_extended": 0.0,
                "end_trimmed": 0.0,
                "boundary_shift": 0.0,
                "max_boundary_shift": 0.0,
                "moved": 0,
                "snapped": 0,
            },
        )
        shifts = _change_shift_stats(change)
        summary["runs"] += 1
        summary["start_delayed"] += shifts["start_delayed"]
        summary["start_earlier"] += shifts["start_earlier"]
        summary["end_extended"] += shifts["end_extended"]
        summary["end_trimmed"] += shifts["end_trimmed"]
        summary["boundary_shift"] += shifts["boundary_shift"]
        summary["max_boundary_shift"] = max(summary["max_boundary_shift"], shifts["boundary_shift"])
        summary["moved"] += int(change["orig_tid"] != change["new_tid"])
        summary["snapped"] += int(change["snap_kind"] is not None)

    return sorted(
        summaries.values(),
        key=lambda summary: (
            -summary["boundary_shift"],
            -summary["runs"],
            -summary["moved"],
            -summary["snapped"],
            -summary["max_boundary_shift"],
            summary["name"],
        ),
    )


def _allocate_lane_tid(pid, tid, lane_idx: int, used_tracks: set[tuple]) -> int | str:
    if type(tid) is int:
        candidate = tid * 1000 + lane_idx
        if (pid, candidate) not in used_tracks:
            used_tracks.add((pid, candidate))
            return candidate

    base = f"{tid} lane {lane_idx}"
    candidate = base
    suffix = 2
    while (pid, candidate) in used_tracks:
        candidate = f"{base} #{suffix}"
        suffix += 1

    used_tracks.add((pid, candidate))
    return candidate


def fix_overlaps(in_path: str, out_path: str, snap_threshold_us: float = SNAP_THRESHOLD_US) -> None:
    if snap_threshold_us < 0:
        raise ValueError("snap_threshold_us must be non-negative")

    with open(in_path) as f:
        trace = json.load(f)

    events = trace.get("traceEvents", trace) if isinstance(trace, dict) else trace

    slices, flows, others = [], [], []
    for e in events:
        ph = e.get("ph")
        if ph == "X" and "dur" in e:
            slices.append(e)
        elif ph in ("s", "t", "f"):
            flows.append(e)
        else:
            others.append(e)

    used_tracks = {
        (e.get("pid"), e.get("tid"))
        for e in events
        if "pid" in e and "tid" in e
    }

    by_track = defaultdict(list)
    for e in slices:
        by_track[(e.get("pid"), e.get("tid"))].append(e)
    print(f"processing {len(by_track)} track(s)")

    lane_names = {}
    lane_tids = {}
    endpoint_rewrites = {}
    snapped_total = 0
    per_track_report = []
    changed_slices = []

    for (pid, tid), group in by_track.items():
        # Outer-first: earliest start, longest dur on ties, so parents are
        # placed before their children.
        group.sort(key=lambda e: (e["ts"], -e["dur"]))
        lanes = []  # each lane is a stack of (end_ts, start_ts)
        moved = 0
        snapped = 0

        for e in group:
            orig_tid = e["tid"]
            orig_start = e["ts"]
            orig_dur = e["dur"]
            orig_end = orig_start + orig_dur
            start = orig_start
            end = start + orig_dur
            placed_lane = None
            snap_kind = None

            for lane_idx, stack in enumerate(lanes):
                placement_resolved = False
                while not placement_resolved:
                    while stack and stack[-1][0] <= start:
                        stack.pop()

                    if not stack:
                        stack.append((end, start))
                        placed_lane = lane_idx
                        placement_resolved = True
                        break

                    top_end, top_start = stack[-1]

                    if lane_idx != 0:
                        if top_start <= start and end <= top_end:
                            stack.append((end, start))
                            placed_lane = lane_idx
                            placement_resolved = True
                        break  # Only snap on the primary lane

                    if start < top_start:
                        start_shift = top_start - start
                        if start_shift < snap_threshold_us:
                            start = top_start
                            e["ts"] = top_start
                            e["dur"] = max(0.0, end - start)
                            snapped += 1
                            snap_kind = "start-snap"
                            continue
                        break

                    if end <= top_end:
                        stack.append((end, start))
                        placed_lane = lane_idx
                        placement_resolved = True
                        break

                    end_shift = end - top_end
                    start_shift = top_end - start

                    if 0 < end_shift <= start_shift and end_shift < snap_threshold_us:
                        end = top_end
                        e["dur"] = max(0.0, end - start)
                        snapped += 1
                        snap_kind = "end-snap"
                    elif 0 < start_shift < snap_threshold_us:
                        start = top_end
                        e["ts"] = top_end
                        e["dur"] = max(0.0, end - start)
                        snapped += 1
                        snap_kind = "start-snap"
                    else:
                        break  # Cannot snap, try next lane

                if placed_lane is not None:
                    break

            if placed_lane is None:
                placed_lane = len(lanes)
                lanes.append([(end, start)])

            if placed_lane > 0:
                lane_key = (pid, tid, placed_lane)
                if lane_key not in lane_tids:
                    lane_tids[lane_key] = _allocate_lane_tid(pid, tid, placed_lane, used_tracks)
                new_tid = lane_tids[lane_key]
                e["tid"] = new_tid
                lane_names[(pid, new_tid)] = (tid, placed_lane)
                moved += 1

            new_start = e["ts"]
            new_dur = e["dur"]
            new_end = new_start + new_dur

            if e["tid"] != orig_tid or new_start != orig_start or new_dur != orig_dur:
                changed_slices.append({
                    "name": e.get("name", "<unnamed>"),
                    "cat": e.get("cat", ""),
                    "pid": pid,
                    "orig_tid": orig_tid,
                    "new_tid": e["tid"],
                    "orig_start": orig_start,
                    "new_start": new_start,
                    "orig_dur": orig_dur,
                    "new_dur": new_dur,
                    "orig_end": orig_end,
                    "new_end": new_end,
                    "snap_kind": snap_kind,
                    "lane_idx": placed_lane,
                })

            if e["tid"] != orig_tid or new_start != orig_start:
                endpoint_rewrites[(pid, orig_tid, orig_start)] = (e["tid"], e["ts"])

        snapped_total += snapped
        if len(lanes) > 1 or snapped:
            per_track_report.append((pid, tid, len(lanes), moved, snapped))

    for pid, tid, n_lanes, moved, snapped in per_track_report:
        print(f"  pid={pid} tid={tid}: {n_lanes} lane(s), {moved} slice(s) moved, {snapped} snapped")
    print(f"snapped {snapped_total} sub-{snap_threshold_us}us crossing(s)")

    if changed_slices:
        total_start_shift = sum(abs(c["new_start"] - c["orig_start"]) for c in changed_slices)
        total_end_shift = sum(abs(c["new_end"] - c["orig_end"]) for c in changed_slices)

        print(
            f"updated {len(changed_slices)} slice(s): "
            f"total |start shift|={_format_us(total_start_shift)}, "
            f"total |end shift|={_format_us(total_end_shift)}, "
            f"total |boundary shift|={_format_us(total_start_shift + total_end_shift)}"
        )

        kernel_changes = [c for c in changed_slices if c["cat"] == "kernel"]
        if kernel_changes:
            kernel_summaries = _summarize_changes_by_name(kernel_changes)
            print(
                "kernel timing summary "
                f"({len(kernel_changes)} run(s) across {len(kernel_summaries)} kernel name(s)):"
            )
            for summary in kernel_summaries:
                print(
                    f"  {summary['name']}: runs={summary['runs']}, "
                    f"total delayed={_format_us(summary['start_delayed'])}, "
                    f"total trimmed={_format_us(summary['end_trimmed'])}, "
                    f"total boundary shift={_format_us(summary['boundary_shift'])}, "
                    f"moved={summary['moved']}, snapped={summary['snapped']}"
                )

        categories = [
            ("moved slices", lambda c: c["cat"] != "kernel" and c["orig_tid"] != c["new_tid"]),
            ("snapped slices", lambda c: c["cat"] != "kernel" and c["snap_kind"] is not None),
        ]

        for title, condition in categories:
            filtered = [c for c in changed_slices if condition(c)]
            if filtered:
                print(f"{title} ({len(filtered)}):")
                for c in filtered:
                    tid_info = f" tid={c['new_tid']}" if title == "snapped slices" else ""
                    print(f"  {c['name']} pid={c['pid']}{tid_info}: {_describe_change(c)}")

    flow_rewrites = 0
    for fe in flows:
        key = (fe.get("pid"), fe.get("tid"), fe.get("ts"))
        if key in endpoint_rewrites:
            fe["tid"], fe["ts"] = endpoint_rewrites[key]
            flow_rewrites += 1

    print(f"rewrote {flow_rewrites} flow endpoint(s) onto rewritten slice starts/lanes")

    for (pid, new_tid), (orig_tid, lane_idx) in lane_names.items():
        others.extend([
            {
                "name": "thread_name", "ph": "M", "pid": pid, "tid": new_tid,
                "args": {"name": f"tid {orig_tid} lane {lane_idx}"},
            },
            {
                "name": "thread_sort_index", "ph": "M", "pid": pid, "tid": new_tid,
                "args": {"sort_index": lane_idx},
            }
        ])

    out_events = others + slices + flows
    if isinstance(trace, dict):
        trace["traceEvents"] = out_events
    else:
        trace = out_events

    with open(out_path, "w") as f:
        json.dump(trace, f, indent=2)
        f.write("\n")

    print(f"wrote {out_path}: {len(slices)} slices, {len(lane_names)} new lanes")
