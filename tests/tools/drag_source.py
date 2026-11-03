"""A small window that starts a real Windows drag of one file when pressed. Used by blackbox.py."""

import json
import sys
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QLabel


class Source(QLabel):
    def __init__(self, path: str):
        super().__init__("Test drag source")
        self.path = path
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(220, 80)
        self.move(40, 40)

    def mousePressEvent(self, _event) -> None:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.path)])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv[:1])
    window = Source(sys.argv[1])
    window.show()

    def announce() -> None:
        ratio = window.devicePixelRatioF()
        center = window.mapToGlobal(window.rect().center())
        Path(sys.argv[2]).write_text(json.dumps({"x": int(center.x() * ratio), "y": int(center.y() * ratio)}), encoding="utf-8")

    QTimer.singleShot(300, announce)
    QTimer.singleShot(20000, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
