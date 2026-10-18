#!/usr/bin/env python3
"""V3 workflow benchmark. Run --help for provisioned-credential arguments."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.workflow import main
if __name__=='__main__':raise SystemExit(main())
