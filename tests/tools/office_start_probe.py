"""Measure how often the office crashes on the first document after starting, without any retry.

  office_start_probe.py <office dir> <cycles> <document> <delay seconds>...
"""

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
work = ROOT / ".build" / "start-probe"
shutil.rmtree(work, ignore_errors=True)
work.mkdir(parents=True)
os.environ["JAMES_VIEWER_DATA_DIR"] = str(work / "data")

from james_viewer.core.office_engine import OfficeWorker


def main() -> None:
    office, cycles, document = Path(sys.argv[1]).resolve(), int(sys.argv[2]), str(Path(sys.argv[3]).resolve())
    for delay in (float(value) for value in sys.argv[4:]):
        crashes = 0
        times = []
        for cycle in range(cycles):
            worker = OfficeWorker(office, 0)
            started = time.perf_counter()
            worker.start()
            time.sleep(delay)
            reply = worker.call("pdf", input=document, output=str(work / f"out-{cycle}.pdf"))
            times.append(time.perf_counter() - started)
            if not reply.get("ok"):
                crashes += 1
                print(f"  delay {delay}: cycle {cycle}: {reply.get('error', '')[:90]}", flush=True)
            worker.stop()
        print(f"delay {delay}s: {crashes} failures in {cycles} starts, average {sum(times) / len(times):.2f}s", flush=True)


if __name__ == "__main__":
    main()
