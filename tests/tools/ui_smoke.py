"""Launch the real window, open documents, and save a screenshot of each page."""

import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("JAMES_VIEWER_DATA_DIR", str(ROOT / ".build" / "data-ui"))
os.environ.setdefault("JAMES_VIEWER_OFFICE_DIR", str(ROOT / ".build" / "lo-full"))

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from james_viewer.ui import theme
from james_viewer.ui.mainwindow import MainWindow


def pump(seconds: float, until=None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    files = sys.argv[2:]
    errors = []
    sys.excepthook = lambda kind, value, tb: errors.append("".join(traceback.format_exception(kind, value, tb)))
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(theme.sans(13))
    app.setStyleSheet(theme.STYLE_SHEET)
    window = MainWindow()
    window.show()
    pump(0.6)
    window.grab().save(str(out / "00-home.png"))
    for number, value in enumerate(files, 1):
        started = time.monotonic()
        before = len(window.views)
        window.open_paths([value])
        opened = pump(30, lambda: len(window.views) > before)
        if not opened:
            print("did not open:", value)
            continue
        document = window.views[-1].document
        ready = pump(180, lambda: not document.preparing and document.ref_at(0) is not None)
        print(f"{Path(value).name}: ready={ready} {time.monotonic() - started:.2f}s pages={len(document.pages)}")
        pump(1.5)
        window.grab().save(str(out / f"{number:02d}-view.png"))
    if window.views:
        window.set_nav("arrange")
        pump(2.0)
        window.grab().save(str(out / "90-arrange.png"))
        window.set_nav("split")
        pump(0.4)
        window.grab().save(str(out / "91-split.png"))
        window.set_nav("protection")
        pump(0.4)
        window.grab().save(str(out / "92-protection.png"))
    print("errors:", len(errors))
    for item in errors[:6]:
        print(item)
    for view in window.views:
        view.document._saved_pages = view.document.pages
        view.document._saved_format = view.document.format
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
