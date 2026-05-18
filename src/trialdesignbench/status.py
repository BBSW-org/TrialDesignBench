"""Shared status reporting types."""

from __future__ import annotations

from collections.abc import Callable

StatusReporter = Callable[[str], None]
