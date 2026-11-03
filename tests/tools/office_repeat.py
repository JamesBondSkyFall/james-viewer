"""Render one deck again and again, each time with a fresh office, and count the failures.

  office_repeat.py <deck.pptx> <times>

Timing crashes only show up over many starts, so one good render proves little. Retrying is turned
off here, so a crash that a user would never see still counts.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
DATA = Path(tempfile.mkdtemp(prefix="Local App Data 测试 "))
os.environ.pop("JAMES_VIEWER_DATA_DIR", None)
os.environ["LOCALAPPDATA"] = str(DATA)

from james_viewer.core import office_engine
from james_viewer.core.errors import ViewerError
from james_viewer.core.office_engine import OfficeEngine

office_engine.ENGINE_ATTEMPTS = 1


def main() -> int:
    deck, times = Path(sys.argv[1]).resolve(), int(sys.argv[2])
    failures: list[str] = []
    seconds: list[float] = []
    output = DATA / "out.pdf"
    for _ in range(times):
        engine = OfficeEngine()
        started = time.monotonic()
        output.unlink(missing_ok=True)
        try:
            engine.to_pdf(deck, output)
            seconds.append(time.monotonic() - started)
        except ViewerError as exc:
            failures.append(str(exc)[-100:])
        finally:
            engine.close()
    average = sum(seconds) / max(len(seconds), 1)
    print(f"{len(failures)} failures in {times} renders, {average:.2f} s each", flush=True)
    for failure in failures[:3]:
        print("  ", failure, flush=True)
    shutil.rmtree(DATA, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
