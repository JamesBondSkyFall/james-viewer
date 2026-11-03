"""Page through a large PDF like a reader, then check that rendering settles.

  render_loop_probe.py <file.pdf> [output.png]

Prints the cache's own byte count next to the real one, how many renders run while the app
should be idle, and how many different pictures the window shows then. A blinking window
shows many; a settled one shows 1.
"""

import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ["JAMES_VIEWER_DATA_DIR"] = str(ROOT / ".build" / "data-loop")

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from james_viewer.ui import theme
from james_viewer.ui.mainwindow import MainWindow


def pump(seconds: float, until=None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until is None


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(theme.STYLE_SHEET)
    window = MainWindow()
    window.resize(1450, 910)
    window.show()
    renders = [0]
    original = window.workspace.pdfium.render

    def counted(*arguments):
        renders[0] += 1
        return original(*arguments)

    window.workspace.pdfium.render = counted
    window.open_paths([sys.argv[1]])
    pump(30, lambda: bool(window.views))
    view = window.views[0]
    document = view.document
    pump(30, lambda: document.ref_at(0) is not None)
    cache = window.workspace.images

    def real_bytes() -> int:
        return sum(p.width() * p.height() * 4 for p in cache._items.values())

    # Read forward a page at a time and sweep the thumbnails, as a person skimming a book would.
    for position in (list(range(0, 130)) + list(range(130, 700, 19)) + [119]) * 2:
        view.go_to(position)
        pump(0.03)
        view.rail.verticalScrollBar().setValue(view.rail.verticalScrollBar().value() + 40)
        pump(0.01)
    view.go_to(119)

    before = renders[0]
    pump(3)
    settle_renders = renders[0] - before
    before = renders[0]
    pictures = set()
    for _ in range(40):
        pump(0.1)
        image = window.grab().toImage()
        pictures.add(hashlib.sha1(bytes(image.constBits())).hexdigest())
    idle_renders = renders[0] - before
    ref = document.ref_at(document.current)
    ratio = view.canvas.devicePixelRatioF()
    rect = view.canvas._page_rect()
    key = cache.key(ref, round(rect.width() * ratio), round(rect.height() * ratio))
    print(f"total renders: {renders[0]}")
    print(f"cache says {cache.used / 1e6:.0f} MB, really holds {real_bytes() / 1e6:.0f} MB in {len(cache._items)} images (limit {cache.limit / 1e6:.0f} MB)")
    print(f"renders while settling (3 s): {settle_renders}")
    print(f"renders while idle (4 s): {idle_renders}")
    print(f"different pictures on screen while idle: {len(pictures)} of 40 looks")
    print(f"current page is drawn sharp: {key in cache._items}")
    if len(sys.argv) > 2:
        window.grab().save(sys.argv[2])
    window.close()
    return 0 if idle_renders == 0 and len(pictures) == 1 and key in cache._items else 1


if __name__ == "__main__":
    raise SystemExit(main())
