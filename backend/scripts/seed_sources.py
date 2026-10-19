#!/usr/bin/env python
"""Dev-friendly wrapper for the idempotent seed (same logic as app.seed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.seed import seed  # noqa: E402

if __name__ == "__main__":
    seed()
