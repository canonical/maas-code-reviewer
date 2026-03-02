"""Backwards-compatibility stub.

This module previously contained the legacy CLI implementation.
It now delegates to ``cli.main()`` so that any existing callers
continue to work.
"""

from lp_ci_tools.cli import main

__all__ = ["main"]
