"""The Soft Paper style: pale neutral surfaces with #5c9d72 as a gradient accent."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap

ACCENT = "#5c9d72"
ACCENT_LIGHT = "#6fb186"
ACCENT_DARK = "#4a8a60"
ACCENT_SOFT = "#e1efe6"
ACCENT_FAINT = "#eef6f1"
BG = "#f0f4f1"
SIDEBAR = "#f7faf8"
PAPER = "#fcfefd"
LINE = "#dfe8e2"
LINE_STRONG = "#cfdcd4"
INK = "#26382d"
MUTED = "#7c8d82"
FAINT = "#9daca2"
STAGE_TOP = "#eef3ef"
STAGE_BOTTOM = "#e6ede8"
DANGER = "#b4544a"
SHADOW = (32, 58, 42)

SANS_FAMILIES = ["Segoe UI", "Microsoft YaHei"]


def sans(pixel_size: float, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    font = QFont()
    font.setFamilies(SANS_FAMILIES)
    font.setPixelSize(int(pixel_size))
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def numeral(pixel_size: float) -> QFont:
    font = QFont("Georgia")
    font.setPixelSize(int(pixel_size))
    font.setItalic(True)
    return font


def accent_gradient(rect: QRectF, horizontal: bool = True) -> QLinearGradient:
    end = QPointF(rect.right(), rect.top()) if horizontal else QPointF(rect.left(), rect.bottom())
    gradient = QLinearGradient(rect.topLeft(), end)
    gradient.setColorAt(0.0, QColor(ACCENT_LIGHT))
    gradient.setColorAt(0.5, QColor(ACCENT))
    gradient.setColorAt(1.0, QColor(ACCENT_DARK))
    return gradient


_GRADIENT_QSS = (
    f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_LIGHT}, stop:0.5 {ACCENT}, stop:1 {ACCENT_DARK})"
)
_GRADIENT_HOVER_QSS = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7cbd93, stop:0.5 #67a97d, stop:1 #53946a)"
)

STYLE_SHEET = f"""
QMainWindow, QDialog {{ background: {BG}; color: {INK}; }}
QWidget {{ color: {INK}; }}
QToolTip {{ background: {INK}; color: #f4f8f5; border: none; padding: 5px 8px; font-size: 12px; }}

#Sidebar {{ background: {SIDEBAR}; border-right: 1px solid {LINE}; }}
#TopBar {{ background: {BG}; border-bottom: 1px solid {LINE}; }}
#Toolbar {{ background: {BG}; border-bottom: 1px solid {LINE}; }}
#Rail {{ background: {SIDEBAR}; border-right: 1px solid {LINE}; }}
#TextPanel {{ background: {SIDEBAR}; border-left: 1px solid {LINE}; }}
#Page {{ background: {BG}; }}

QPushButton {{
    background: {PAPER}; border: 1px solid {LINE}; border-radius: 8px;
    padding: 8px 14px; color: {INK};
}}
QPushButton:hover {{ background: #f4f9f6; border-color: {LINE_STRONG}; }}
QPushButton:pressed {{ background: {ACCENT_FAINT}; }}
QPushButton:disabled {{ color: #b2bdb6; background: #f5f8f6; border-color: #e9efeb; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton[primary="true"] {{
    background: {_GRADIENT_QSS}; color: white; border: none; font-weight: 600; padding: 9px 16px;
}}
QPushButton[primary="true"]:hover {{ background: {_GRADIENT_HOVER_QSS}; }}
QPushButton[primary="true"]:pressed {{ background: {ACCENT_DARK}; }}
QPushButton[primary="true"]:disabled {{ background: #c9d8ce; color: #f2f6f3; }}
QPushButton[danger="true"] {{ color: {DANGER}; }}

QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 6px; color: {MUTED}; }}
QToolButton:hover {{ background: {ACCENT_FAINT}; color: {INK}; }}
QToolButton:pressed {{ background: {ACCENT_SOFT}; }}
QToolButton:checked {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}
QToolButton:disabled {{ color: #c3cdc7; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QPlainTextEdit, QComboBox {{
    background: {PAPER}; border: 1px solid {LINE}; border-radius: 8px; padding: 8px 10px;
    selection-background-color: {ACCENT}; selection-color: white;
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}

QRadioButton, QCheckBox {{ spacing: 8px; }}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {LINE_STRONG}; background: {PAPER};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator:checked, QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT_DARK}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 3px; }}
QScrollBar::handle {{ background: #cdd9d1; border-radius: 2px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: #a9bbb0; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; width: 0; height: 0;
}}

QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 9px 12px 9px 15px; margin-right: 6px;
    border-radius: 10px; min-width: 96px; max-width: 220px; border: 1px solid transparent;
}}
QTabBar::tab:hover {{ background: #e8efea; color: {INK}; }}
QTabBar::tab:selected {{ background: white; color: {INK}; border: 1px solid {LINE}; font-weight: 500; }}

QListView {{ background: transparent; border: none; outline: none; }}

QMenu {{ background: {PAPER}; border: 1px solid {LINE}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {INK}; }}
QMenu::item:disabled {{ color: #b2bdb6; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}

QMessageBox {{ background: {PAPER}; }}
QMessageBox QLabel {{ color: {INK}; }}
QProgressBar {{ background: {ACCENT_SOFT}; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: {_GRADIENT_QSS}; border-radius: 2px; }}
"""

_shadow_cache: dict[tuple, QPixmap] = {}


def _blur_alpha(image: QImage, radius: int) -> QImage:
    """A fast box blur repeated three times, which closely matches a Gaussian."""
    width, height = image.width(), image.height()
    data = bytearray(image.constBits().tobytes())
    stride = image.bytesPerLine()
    if radius < 1:
        return image

    def blur_lines(buffer, line_count, line_length, line_step, item_step):
        window = radius * 2 + 1
        for line in range(line_count):
            base = line * line_step
            values = [buffer[base + i * item_step] for i in range(line_length)]
            total = values[0] * (radius + 1) + sum(values[min(i, line_length - 1)] for i in range(1, radius + 1))
            for i in range(line_length):
                buffer[base + i * item_step] = total // window
                total += values[min(i + radius + 1, line_length - 1)] - values[max(i - radius, 0)]

    for _ in range(3):
        blur_lines(data, height, width, stride, 1)
        blur_lines(data, width, height, 1, stride)
    return QImage(bytes(data), width, height, stride, QImage.Format.Format_Alpha8).copy()


def soft_shadow(painter: QPainter, rect: QRectF, radius: float, blur: int, strength: float, dy: float = 6.0) -> None:
    """Draw a soft drop shadow. It is cached as nine slices, so it stays cheap at any size."""
    key = (round(radius), blur, round(strength * 100))
    corner = int(radius) + blur * 2
    tile = _shadow_cache.get(key)
    if tile is None:
        size = corner * 2 + 8
        mask = QImage(size, size, QImage.Format.Format_Alpha8)
        mask.fill(0)
        shape = QPainter(mask)
        shape.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape.setPen(Qt.PenStyle.NoPen)
        shape.setBrush(QColor(0, 0, 0, int(255 * strength)))
        inner = QRectF(blur * 2, blur * 2, size - blur * 4, size - blur * 4)
        shape.drawRoundedRect(inner, radius, radius)
        shape.end()
        blurred = _blur_alpha(mask, max(1, blur // 2))
        colored = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        colored.fill(QColor(*SHADOW))
        tint = QPainter(colored)
        tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        tint.drawImage(0, 0, blurred)
        tint.end()
        tile = QPixmap.fromImage(colored)
        _shadow_cache[key] = tile

    outset = blur * 2
    target = rect.adjusted(-outset, -outset + dy, outset, outset + dy)
    size = tile.width()
    c = corner
    x0, y0, x1, y1 = target.left(), target.top(), target.right(), target.bottom()
    middle_w = max(0.0, target.width() - 2 * c)
    middle_h = max(0.0, target.height() - 2 * c)
    m = size - 2 * c
    pieces = [
        (QRectF(x0, y0, c, c), QRectF(0, 0, c, c)),
        (QRectF(x1 - c, y0, c, c), QRectF(size - c, 0, c, c)),
        (QRectF(x0, y1 - c, c, c), QRectF(0, size - c, c, c)),
        (QRectF(x1 - c, y1 - c, c, c), QRectF(size - c, size - c, c, c)),
        (QRectF(x0 + c, y0, middle_w, c), QRectF(c, 0, m, c)),
        (QRectF(x0 + c, y1 - c, middle_w, c), QRectF(c, size - c, m, c)),
        (QRectF(x0, y0 + c, c, middle_h), QRectF(0, c, c, m)),
        (QRectF(x1 - c, y0 + c, c, middle_h), QRectF(size - c, c, c, m)),
    ]
    for destination, source in pieces:
        if destination.width() > 0 and destination.height() > 0:
            painter.drawPixmap(destination, tile, source)
