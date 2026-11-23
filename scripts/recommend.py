#!/usr/bin/env python3
"""Claude package entrypoint for the shared, self-contained recommender."""
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codex/scripts"))
helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / "codex/scripts/recommend.py"))
sys.exit(helper["main"](default_host="claude"))
