"""Start and stop the office engine repeatedly to measure how often it crashes.

Usage: office_stress.py <office dir> <cycles> <fresh profile: 0 or 1> <files...>
"""

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
work = ROOT / ".build" / "stress"
shutil.rmtree(work, ignore_errors=True)
work.mkdir(parents=True)
os.environ["JAMES_VIEWER_DATA_DIR"] = str(work / "data")

from james_viewer.core.office_engine import OfficeEngine, reset_profiles


def main() -> None:
    office = Path(sys.argv[1]).resolve()
    cycles, fresh = int(sys.argv[2]), sys.argv[3] == "1"
    files = [Path(p) for p in sys.argv[4:]]
    renders = failures = 0
    for cycle in range(cycles):
        if fresh:
            reset_profiles()
        engine = OfficeEngine(office)
        started = time.perf_counter()
        for item in files:
            try:
                reply = engine.to_pdf(item, work / f"{cycle}-{item.stem}.pdf")
                renders += 1
                assert reply["pages"] > 0
            except Exception as exc:
                failures += 1
                print(f"cycle {cycle} {item.name}: FAILED {str(exc)[:120]}", flush=True)
        engine.close()
        print(f"cycle {cycle}: {time.perf_counter() - started:.1f}s", flush=True)
    print(f"renders ok={renders} failed={failures}", flush=True)


if __name__ == "__main__":
    main()
