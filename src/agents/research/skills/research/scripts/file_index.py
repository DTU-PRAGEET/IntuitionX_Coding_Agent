#!/usr/bin/env python3
"""Shim — delegates to src.core.skill_scripts.file_index."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from src.core.skill_scripts.file_index import main

if __name__ == "__main__":
    sys.exit(main())
