#!/usr/bin/env python3
"""Claude package entrypoint for the shared, self-contained recommender."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] / "codex/scripts/recommend.py"), run_name="__main__")
