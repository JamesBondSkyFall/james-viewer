"""Write the Windows icon from the same drawing the app uses for its mark."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap

from james_viewer.ui import icons


def main(out: Path) -> None:
    app = QGuiApplication(sys.argv[:1])
    images = []
    for size in (256, 128, 64, 48, 32, 24, 16):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        icons.draw_mark(painter, QRectF(0, 0, size, size))
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        from io import BytesIO

        images.append(Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA"))
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(out, format="ICO", append_images=images[1:], sizes=[(i.width, i.height) for i in images])
    images[0].save(out.with_suffix(".png"))
    print("wrote", out, out.stat().st_size)
    del app


if __name__ == "__main__":
    main(Path(sys.argv[1]))
