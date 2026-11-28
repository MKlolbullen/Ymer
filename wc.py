#!/usr/bin/env python3
"""Run the word counter from the repository root: python wc.py FILE [--top N]."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from app.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
