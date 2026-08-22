#!/usr/bin/env python3
"""Fail-open Codex command-hook entry point.

Reads exactly one documented hook JSON object from stdin and always exits zero.
It intentionally prints an empty JSON object: the adapter observes host work
but never blocks, rewrites, approves, or otherwise changes that work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from capture_support import diagnostic, process


def main() -> int:
    root = Path(os.environ.get("AXEL_IMPROVE_ROOT", ".axel-improve"))
    try:
        value = json.load(sys.stdin)
        if not isinstance(value, dict):
            raise ValueError("hook input is not an object")
        process(value, root)
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        diagnostic(root, "malformed hook payload")
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
