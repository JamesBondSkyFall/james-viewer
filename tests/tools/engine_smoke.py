"""Exercise the office engine directly and report timings. Usage: engine_smoke.py <deck> <out dir>"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("JAMES_VIEWER_DATA_DIR", str(ROOT / ".build" / "data-smoke"))

from james_viewer.core.office_engine import OfficeEngine
from james_viewer.core.pptx_engine import read_deck_info


def main() -> None:
    deck = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    engine = OfficeEngine()
    print("office:", engine.office, flush=True)
    info = read_deck_info(deck)
    print("slides:", len(info.slides), flush=True)
    started = time.perf_counter()
    done = []

    def on_chunk(indexes, pdf):
        done.append((indexes[0], indexes[-1], pdf.name))
        print(f"{time.perf_counter() - started:7.2f}s chunk {indexes[0] + 1}-{indexes[-1] + 1} -> {pdf.name}", flush=True)

    try:
        engine.render_deck(deck, len(info.slides), out, on_chunk)
        print(f"total {time.perf_counter() - started:.2f}s", flush=True)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
