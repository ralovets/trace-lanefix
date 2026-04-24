"""Command-line interface for trace-lanefix."""

import argparse

from .core import SNAP_THRESHOLD_US, fix_overlaps


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-lanefix",
        description="Rewrite a Chrome/Kineto trace so Perfetto can render overlapping slices.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="trace.json",
        help="input Chrome trace JSON path (default: trace.json)",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default="trace.fixed.json",
        help="output fixed trace JSON path (default: trace.fixed.json)",
    )
    parser.add_argument(
        "--snap-threshold-us",
        type=_non_negative_float,
        default=SNAP_THRESHOLD_US,
        help=(
            "maximum timestamp crossing, in microseconds, to snap in place "
            f"instead of moving to a synthetic lane (default: {SNAP_THRESHOLD_US})"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fix_overlaps(args.input, args.output, snap_threshold_us=args.snap_threshold_us)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
