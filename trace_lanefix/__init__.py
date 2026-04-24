"""Utilities for making Chrome/Kineto traces render cleanly in Perfetto."""

from .core import SNAP_THRESHOLD_US, fix_overlaps

__all__ = ["SNAP_THRESHOLD_US", "fix_overlaps"]
