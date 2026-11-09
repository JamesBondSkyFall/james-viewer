"""Drawing a text box's words, for the picture a PDF text box annotation shows."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter

from . import theme

SCALE = 4  # pixels per point, so the words stay sharp when zoomed in
MAX_WIDTH = 280.0
PADDING = 3.0


def _font(size: float) -> QFont:
    font = QFont()
    font.setFamilies(theme.SANS_FAMILIES)
    font.setPixelSize(max(1, round(size)))
    return font


def measure(text: str, size: float) -> tuple[float, float]:
    """Width and height in points of a text box that holds `text` without clipping it."""
    metrics = QFontMetricsF(_font(size))
    box = metrics.boundingRect(QRectF(0, 0, MAX_WIDTH, 100000), Qt.TextFlag.TextWordWrap, text)
    return math.ceil(box.width() + PADDING * 2), math.ceil(box.height() + PADDING * 2)


def paint_text(text: str, size: float, width: float, height: float) -> tuple[bytes, int, int]:
    """RGBA pixels of the words on a clear background. Safe to call from a worker thread."""
    width_px, height_px = max(1, math.ceil(width * SCALE)), max(1, math.ceil(height * SCALE))
    image = QImage(width_px, height_px, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(_font(size * SCALE))
    painter.setPen(QColor(theme.INK))
    inset = PADDING * SCALE
    painter.drawText(QRectF(inset, inset, width_px - inset * 2, height_px - inset * 2), Qt.TextFlag.TextWordWrap, text)
    painter.end()
    return bytes(image.constBits())[: width_px * height_px * 4], width_px, height_px
