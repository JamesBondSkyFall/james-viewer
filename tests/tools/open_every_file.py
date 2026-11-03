"""Open each file in the real window, as a user would, and check every page.

  open_every_file.py <results.json> <file or folder>...

Each file gets a clean tab. A file passes when it opens with no error message, every page gets its
image, and the source is unchanged. Blank pages are listed, because some are blank on purpose.
"""

import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / ".build" / "Local App Data 测试"
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir(parents=True)
os.environ.pop("JAMES_VIEWER_DATA_DIR", None)
os.environ["LOCALAPPDATA"] = str(DATA)  # so the app builds its normal data folder, "James Viewer", in here

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog

from james_viewer.core.sniff import OPEN_EXTENSIONS
from james_viewer.ui import theme, widgets
from james_viewer.ui.mainwindow import MainWindow
from james_viewer.ui.markdown_view import MarkdownTab

SKIP_FOLDERS = {".git", ".venv", "node_modules", "__pycache__"}
TIMEOUT = 420.0


def pump(seconds: float, until=None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until is None


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_files(values: list[str]) -> list[Path]:
    found = []
    for value in values:
        path = Path(value)
        if path.is_file():
            found.append(path)
            continue
        for item in sorted(path.rglob("*")):
            if item.is_file() and item.suffix.lower() in OPEN_EXTENSIONS and not SKIP_FOLDERS & set(item.relative_to(path).parts):
                found.append(item)
    return found


def blank_pages(window, document) -> list[int]:
    blank = []
    for position in range(len(document.pages)):
        ref = document.ref_at(position)
        data, width, height, stride, channels = window.workspace.pdfium.render(ref.pdf, ref.password, ref.page, 200, 200)
        image = QImage(data, width, height, stride, QImage.Format.Format_RGB32 if channels == 4 else QImage.Format.Format_BGR888)
        tones = {QColor(image.pixel(x, y)).lightness() // 8 for y in range(0, height, 3) for x in range(0, width, 3)}
        if len(tones) <= 1:
            blank.append(position + 1)
    return blank


def main() -> int:
    out = Path(sys.argv[1])
    files = find_files(sys.argv[2:])
    errors: list[str] = []

    def note_error(kind, value, tb) -> None:
        text = "".join(traceback.format_exception(kind, value, tb))
        errors.append(text[-1500:])
        print(text, file=sys.stderr, flush=True)

    sys.excepthook = note_error
    told: list[str] = []
    widgets.tell = lambda _parent, title, text="": told.append(f"{title}: {text}")
    widgets.ask = lambda *_args, **_kwargs: False
    widgets.PasswordDialog.exec = lambda self: (told.append("asked for a password"), QDialog.DialogCode.Rejected)[1]
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(theme.sans(13))
    app.setStyleSheet(theme.STYLE_SHEET)
    window = MainWindow()
    window.show()
    pump(0.3)
    results = []
    for number, path in enumerate(files, 1):
        before = sha(path)
        told.clear()
        errors.clear()
        started = time.monotonic()
        window.open_paths([str(path)])
        pump(TIMEOUT, lambda: bool(window.views) or bool(told))
        entry = {"file": str(path), "pages": 0, "seconds": 0.0, "told": [], "errors": [], "blank": [], "notes": [], "passed": False}
        if window.views:
            view = window.views[0]
            document = view.document
            if isinstance(view, MarkdownTab):
                pump(5, lambda: view.canvas._rendered_width > 0 and not view.canvas._timer.isActive())
                entry["pages"] = 1
                assets = document.page.assets
                entry["notes"] = [f"{asset.kind}: {asset.source[:60]}: {asset.error}" for asset in assets if asset.error]
                entry["assets"] = len(assets)
            else:
                ready = pump(TIMEOUT, lambda: not document.preparing and all(document.ref_at(i) is not None for i in range(len(document.pages))) or bool(told))
                entry["pages"] = len(document.pages)
                entry["ready"] = ready
                entry["notes"] = [note.title for note in document.notes]
                missing = [i + 1 for i in range(len(document.pages)) if document.ref_at(i) is None]
                entry["missing"] = missing
                if not missing and document.pages:
                    entry["blank"] = blank_pages(window, document)
            pump(0.2)
            while window.views:
                window.close_tab(0)
            pump(0.1)
        entry["seconds"] = round(time.monotonic() - started, 1)
        entry["told"] = list(told)
        entry["errors"] = list(errors)
        entry["unchanged"] = sha(path) == before
        entry["passed"] = bool(entry["pages"]) and not told and not errors and entry["unchanged"] and not entry.get("missing")
        results.append(entry)
        mark = "PASS" if entry["passed"] else "FAIL"
        extra = f" blank={entry['blank']}" if entry["blank"] else ""
        print(f"{mark} {number}/{len(files)} {entry['pages']:>4}p {entry['seconds']:>6}s {path.name}{extra}", flush=True)
        for line in entry["told"] + entry["errors"]:
            print("     ", line.replace("\n", " ")[:700], flush=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    window.close()
    failed = [entry["file"] for entry in results if not entry["passed"]]
    print(f"{len(results) - len(failed)} of {len(results)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
