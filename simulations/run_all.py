#!/usr/bin/env python3
"""Run every scenario in order and report a tally.

Each scenario is independent — run one on its own with, for example:
    python3 simulations/scenario_5.py
"""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
scenarios = sorted(HERE.glob("scenario_*.py"))
results = []
for s in scenarios:
    r = subprocess.run([sys.executable, str(s)])
    results.append((s.name, r.returncode == 0))

print("\n" + "=" * 60)
for name, ok in results:
    print(f"  {name:<20} {'PASS' if ok else 'FAIL'}")
passed = sum(1 for _, ok in results if ok)
print(f"\n  {passed}/{len(results)} scenarios passed")
sys.exit(0 if passed == len(results) else 1)
