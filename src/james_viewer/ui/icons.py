"""Line icons drawn with the same stroke as the reference style, plus the James Viewer mark."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap

from . import theme

_R = "rect"
_L = "line"
_C = "circle"
_ICONS = {
    "view": [(_R, 3, 5, 18, 14, 2.5), (_L, 3, 9, 21, 9)],
    "arrange": [(_R, 3, 3, 8, 8, 1.8), (_R, 13, 3, 8, 8, 1.8), (_R, 3, 13, 8, 8, 1.8), (_R, 13, 13, 8, 8, 1.8)],
    "panel": [(_R, 3, 4, 18, 16, 2.5), (_L, 9.5, 4, 9.5, 20)],
    "split": [(_L, 12, 3, 12, 21), (_L, 7, 8, 3, 12), (_L, 3, 12, 7, 16), (_L, 17, 8, 21, 12), (_L, 21, 12, 17, 16)],
    "lock": [(_R, 5, 11, 14, 10, 2.5), (_L, 8, 11, 8, 7.5), (_L, 16, 11, 16, 7.5), (_L, 8, 7.5, 10.5, 4), (_L, 16, 7.5, 13.5, 4), (_L, 10.5, 4, 13.5, 4)],
    "plus": [(_L, 12, 5, 12, 19), (_L, 5, 12, 19, 12)],
    "minus": [(_L, 5, 12, 19, 12)],
    "fit": [(_R, 4, 6, 16, 12, 2), (_L, 8, 12, 16, 12)],
    "undo": [(_L, 4, 10, 15, 10), (_L, 4, 10, 8, 6), (_L, 4, 10, 8, 14), (_L, 15, 10, 19, 13), (_L, 19, 13, 19, 17)],
    "redo": [(_L, 20, 10, 9, 10), (_L, 20, 10, 16, 6), (_L, 20, 10, 16, 14), (_L, 9, 10, 5, 13), (_L, 5, 13, 5, 17)],
    "full": [(_L, 4, 9, 4, 4), (_L, 4, 4, 9, 4), (_L, 15, 4, 20, 4), (_L, 20, 4, 20, 9), (_L, 20, 15, 20, 20), (_L, 20, 20, 15, 20), (_L, 9, 20, 4, 20), (_L, 4, 20, 4, 15)],
    "open": [(_L, 3, 7, 3, 19), (_L, 3, 19, 21, 19), (_L, 21, 19, 21, 9), (_L, 21, 9, 11, 9), (_L, 11, 9, 9, 6), (_L, 9, 6, 4, 6)],
    "save": [(_R, 4, 4, 16, 16, 2.5), (_L, 8, 4, 8, 9), (_L, 8, 9, 15, 9), (_L, 15, 9, 15, 4), (_L, 8, 20, 8, 14), (_L, 8, 14, 16, 14), (_L, 16, 14, 16, 20)],
    "copy": [(_R, 8, 8, 12, 12, 2), (_L, 16, 8, 16, 5), (_L, 16, 5, 4, 5), (_L, 4, 5, 4, 16), (_L, 4, 16, 8, 16)],
    "trash": [(_L, 4, 7, 20, 7), (_L, 9, 7, 9, 4), (_L, 9, 4, 15, 4), (_L, 15, 4, 15, 7), (_L, 6, 7, 7, 20), (_L, 7, 20, 17, 20), (_L, 17, 20, 18, 7), (_L, 10, 11, 10, 16), (_L, 14, 11, 14, 16)],
    "text": [(_L, 5, 6, 19, 6), (_L, 12, 6, 12, 19), (_L, 9, 19, 15, 19)],
    "help": [(_C, 12, 12, 9), (_L, 12, 17, 12, 17.1), (_L, 9.5, 9.5, 11, 8), (_L, 11, 8, 13.5, 8), (_L, 13.5, 8, 14.5, 10), (_L, 14.5, 10, 12, 13), (_L, 12, 13, 12, 14)],
    "doc": [(_L, 6, 3, 14, 3), (_L, 14, 3, 19, 8), (_L, 19, 8, 19, 21), (_L, 19, 21, 6, 21), (_L, 6, 21, 6, 3), (_L, 14, 3, 14, 8), (_L, 14, 8, 19, 8)],
    "x": [(_L, 7, 7, 17, 17), (_L, 17, 7, 7, 17)],
    "up": [(_L, 6, 14, 12, 8), (_L, 12, 8, 18, 14)],
    "down": [(_L, 6, 10, 12, 16), (_L, 12, 16, 18, 10)],
    "check": [(_L, 5, 13, 10, 18), (_L, 10, 18, 19, 7)],
}


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: str, width: float = 1.6) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 24, rect.height() / 24)
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for item in _ICONS[name]:
        if item[0] == _R:
            painter.drawRoundedRect(QRectF(item[1], item[2], item[3], item[4]), item[5], item[5])
        elif item[0] == _C:
            painter.drawEllipse(QPointF(item[1], item[2]), item[3], item[3])
        else:
            painter.drawLine(QPointF(item[1], item[2]), QPointF(item[3], item[4]))
    painter.restore()


def icon(name: str, color: str = theme.MUTED, size: int = 20, disabled: str = "#c3cdc7") -> QIcon:
    result = QIcon()
    for ratio in (1, 2):
        for mode, tone in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, disabled)):
            pixmap = QPixmap(QSize(size * ratio, size * ratio))
            pixmap.fill(Qt.GlobalColor.transparent)
            pixmap.setDevicePixelRatio(ratio)
            painter = QPainter(pixmap)
            draw_icon(painter, name, QRectF(0, 0, size, size), tone)
            painter.end()
            result.addPixmap(pixmap, mode)
    return result


def draw_mark(painter: QPainter, rect: QRectF, on_dark: bool = False) -> None:
    """The James Viewer mark: three sheets receding in depth."""
    size = min(rect.width(), rect.height())
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.topLeft())
    sheets = ((size * 0.34, size * 0.02, 0.35), (size * 0.17, size * 0.13, 0.62), (0.0, size * 0.24, 1.0))
    for dx, dy, alpha in sheets:
        sheet = QRectF(dx, dy, size * 0.66, size * 0.74)
        gradient = QLinearGradient(sheet.topLeft(), sheet.bottomRight())
        first = QColor("#ffffff" if on_dark else theme.ACCENT_LIGHT)
        last = QColor("#dff0e5" if on_dark else theme.ACCENT_DARK)
        first.setAlphaF(alpha)
        last.setAlphaF(alpha)
        gradient.setColorAt(0, first)
        gradient.setColorAt(1, last)
        painter.setBrush(gradient)
        edge = QColor("#ffffff")
        edge.setAlphaF(0.8)
        painter.setPen(QPen(edge, max(1.0, size * 0.03)))
        painter.drawRoundedRect(sheet, size * 0.1, size * 0.1)
    painter.restore()


def app_icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        draw_mark(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result
