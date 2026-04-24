# trace-lanefix

`trace-lanefix` rewrites Chrome/Kineto trace JSON so Perfetto can render slices
that would otherwise be dropped because of crossing overlaps on the same
`(pid, tid)` track.

It keeps properly nested slices on the same track, snaps tiny timestamp-boundary
crossings, and moves genuine overlaps to synthetic lanes with collision-free
thread ids.

## Before / After

Before:

<img src="https://raw.githubusercontent.com/ralovets/trace-lanefix/main/assets/profile-before.png" alt="Profile before lane fix" width="100%">

After:

<img src="https://raw.githubusercontent.com/ralovets/trace-lanefix/main/assets/profile-after.png" alt="Profile after lane fix" width="100%">

In Perfetto's trace overview, the rewritten trace reports `TRACE ERRORS=0`
instead of `TRACE ERRORS=6`, while `IMPORT ERRORS` and `DATA LOSSES` remain
at `0`. In this example, the JSON trace size changed from `452.76 KiB` before
rewriting (`463,629` bytes) to `653.96 KiB` after rewriting (`669,651`
bytes) because the output is pretty-printed for readability.

## CLI

```bash
trace-lanefix input.json output.fixed.json
```

Tiny crossings below `5.0us` are snapped by default. To use a different snap
threshold:

```bash
trace-lanefix --snap-threshold-us 2.5 input.json output.fixed.json
```

Example output:

This run shows the two repair paths: tiny sub-5.0us timestamp crossings are
snapped in place, while one genuine overlap is moved to a synthetic lane. Flow
endpoints are then rewritten to follow the adjusted slice starts and lanes.

```text
trace-lanefix trace.json trace_fixed.json
processing 3 track(s)
  pid=7334 tid=7334: 2 lane(s), 1 slice(s) moved, 11 snapped
  pid=0 tid=7: 1 lane(s), 0 slice(s) moved, 5 snapped
snapped 16 sub-5.0us crossing(s)
updated 15 slice(s): total |start shift|=6.195us, total |end shift|=5.240us, total |boundary shift|=11.436us
kernel timing summary (5 run(s) across 3 kernel name(s)):
  nvjet_sm120_tst_mma_256x112x64_2_64x56x64_tmaAB_bz_TNNN: runs=2, total delayed=2.848us, total trimmed=0.000us, total boundary shift=2.848us, moved=0, snapped=2
  nvjet_sm120_tst_mma_96x256x64_2_48x64x64_tmaAB_bz_TNNN: runs=2, total delayed=480ns, total trimmed=0.000us, total boundary shift=480ns, moved=0, snapped=2
  nvjet_sm120_tst_mma_128x256x64_2_64x64x64_tmaAB_bz_TNNN: runs=1, total delayed=384ns, total trimmed=0.000us, total boundary shift=384ns, moved=0, snapped=1
moved slices (1):
  ProfilerStep#5 pid=7334: moved to lane 1 (tid 7334 -> 7334001)
snapped slices (9):
  layer0_forward pid=7334 tid=7334: start delayed by 2.483us
  torch/autograd/profiler.py(843): __exit__ pid=7334 tid=7334: end trimmed by 2.855us
  torch/_ops.py(1079): __call__ pid=7334 tid=7334: end trimmed by 1.222us
  <built-in method  of pybind11_builtins.pybind11_detail_function_record_v1_system_libstdcpp_gxx_abi_1xxx_use_cxx11_abi_1 object at 0x719b5a7004b0> pid=7334 tid=7334: end trimmed by 1.162us
  contextlib.py(141): __exit__ pid=7334 tid=7334: end trimmed by 0.2ns
  profiling.py(19): trace pid=7334 tid=7334: end trimmed by 0.2ns
  torch/profiler/profiler.py(912): _transit_action pid=7334 tid=7334: end trimmed by 0.2ns
  torch/cuda/__init__.py(1161): synchronize pid=7334 tid=7334: end trimmed by 0.2ns
  torch/cuda/__init__.py(609): __exit__ pid=7334 tid=7334: end trimmed by 0.2ns
rewrote 5 flow endpoint(s) onto rewritten slice starts/lanes
wrote trace_fixed.json: 988 slices, 1 new lanes
```

## Python API

```python
from trace_lanefix import fix_overlaps

fix_overlaps("input.json", "output.fixed.json")
fix_overlaps("input.json", "output.fixed.json", snap_threshold_us=2.5)
```
