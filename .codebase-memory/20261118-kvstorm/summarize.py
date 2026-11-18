#!/usr/bin/env python3
"""Summarize one arm: python3 -I summarize.py <run-dir>. Thin wrapper over compare.py so the
whole pipeline uses ONE attribution method (finish-line segmentation, see kvparse.py)."""
import os, subprocess, sys
d = os.path.abspath(sys.argv[1].rstrip("/"))
n = sum(1 for _ in open(os.path.join(d, "requests.jsonl")))
here = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.run([sys.executable, "-I", os.path.join(here, "compare.py"),
                         os.path.dirname(d), str(n), os.path.basename(d)]).returncode)
