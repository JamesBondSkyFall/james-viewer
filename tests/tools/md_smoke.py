"""Open a Markdown file in the real window and save screenshots. The file is only read."""

import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("JAMES_VIEWER_DATA_DIR", str(ROOT / ".build" / "data-md"))

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from james_viewer.ui import theme
from james_viewer.ui.mainwindow import MainWindow


def pump(seconds, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None


def main() -> int:
    path, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(theme.sans(13))
    app.setStyleSheet(theme.STYLE_SHEET)
    window = MainWindow()
    window.show()
    pump(0.3)
    started = time.monotonic()
    window.open_paths([str(path)])
    assert pump(120, lambda: bool(window.views)), "did not open"
    print(f"opened in {time.monotonic() - started:.2f}s")
    view = window.views[-1]
    page = view.document.page
    pump(0.8)
    print(f"render finished at {time.monotonic() - started:.2f}s")
    maths = [a for a in page.assets if a.kind == "math"]
    images = [a for a in page.assets if a.kind == "image"]
    print("headings", len(page.headings), "images", len(images), "image errors", [a.source for a in images if a.error][:5])
    print("math", len(maths), "math errors", [(a.source, a.error) for a in maths if a.error][:8])
    print("notes", [n.title for n in page.notes])
    leftovers = view.canvas.browser.toPlainText()
    print("literal ** left:", leftovers.count("**"), "literal == left:", leftovers.count("=="), "literal $ left:", leftovers.count("$"))
    window.grab().save(str(out / "md-1-top.png"))
    reader = view.canvas
    bar = reader.browser.verticalScrollBar()
    text = reader.browser.toPlainText()
    document = reader.browser.document()

    def show(needle: str, label: str) -> None:
        """Scroll a specific passage into view, so each kind of content gets looked at."""
        position = document.toPlainText().find(needle)
        cursor = reader.browser.textCursor()
        cursor.setPosition(max(position, 0))
        bar.setValue(bar.value() + reader.browser.cursorRect(cursor).top() - 30)
        pump(0.5)
        print(label, "found" if position >= 0 else "NOT FOUND", needle)
        window.grab().save(str(out / f"md-{label}.png"))

    for number, needle in enumerate(sys.argv[3:], 2):
        show(needle, f"{number}-view")
    print("chars", len(text))
    bar.setValue(bar.maximum() // 3)
    pump(0.3)
    for zoom in (1.21, 1.5, 2.0, 0.75, 1.0):
        started = time.perf_counter()
        reader.set_zoom(zoom)
        reader._timer.stop()
        reader.render()
        render_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        pump(0.15)
        print(f"zoom {reader.zoom_text()}: render {render_ms:.0f} ms, then paint {(time.perf_counter() - started) * 1000 - 150:.0f} ms over the wait")
        if zoom in (1.5, 0.75):
            window.grab().save(str(out / f"md-zoom-{round(zoom * 100)}.png"))
    pump(0.3)
    view.toggle_layout()
    pump(0.4)
    window.grab().save(str(out / "md-8-layout-hidden.png"))
    view.toggle_layout()
    window.present()
    pump(1.2)
    window.presenter.grab().save(str(out / "md-9-fullscreen.png"))
    print("full screen", window.presenter.isFullScreen(), window.presenter.geometry().getRect(), "screen", window.screen().geometry().getRect())
    window.presenter.close()
    pump(0.4)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    print("file unchanged:", before == after, after[:16])
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
