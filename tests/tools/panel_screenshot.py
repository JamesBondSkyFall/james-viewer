"""Save one picture of the real window with the control panel shown and hidden.

  panel_screenshot.py <file> <page> <output.png>
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ["JAMES_VIEWER_DATA_DIR"] = str(ROOT / ".build" / "data-panel")

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from james_viewer.ui import theme
from james_viewer.ui.mainwindow import MainWindow


def pump(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def main() -> int:
    source, page, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(theme.sans(13))
    app.setStyleSheet(theme.STYLE_SHEET)
    window = MainWindow()
    window.resize(1400, 880)
    window.set_control_panel(True)
    window.show()
    window.open_paths([source])
    while not window.views:
        pump(0.05)
    window.views[0].go_to(page - 1)
    pump(2.0)
    shown = window.grab().toImage()
    window.set_control_panel(False)
    pump(1.5)
    hidden = window.grab().toImage()
    window.set_control_panel(True)
    gap = 60
    shown.setDevicePixelRatio(1)
    hidden.setDevicePixelRatio(1)
    picture = QImage(shown.width() * 2 + gap, shown.height(), QImage.Format.Format_RGB32)
    picture.setDevicePixelRatio(1)
    picture.fill(QColor("#ffffff"))
    painter = QPainter(picture)
    painter.drawImage(0, 0, shown)
    painter.drawImage(shown.width() + gap, 0, hidden)
    painter.end()
    picture.save(str(out))
    print(picture.width(), picture.height(), "corner mark at", window.corner_mark.geometry().getRect())
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
