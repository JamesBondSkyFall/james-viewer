"""Render the three proposed visual styles as still images, using real slide pages.

Run with: python tools/design_options.py <deck.pdf> <output folder>
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pypdfium2
from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QTransform,
)

W, H = 1600, 1000
ACCENT = "#5c9d72"


@dataclass
class Style:
    key: str
    title: str
    subtitle: str
    bg: str
    sidebar: str
    panel: str
    panel_line: str
    ink: str
    muted: str
    stage_top: str
    stage_bottom: str
    grad_a: str
    grad_b: str
    active_bg: str
    tab_bg: str
    dark_stage: bool = False
    glass_header: bool = False


STYLES = [
    Style(
        "A-soft-paper", "Style A  Soft Paper", "The closest to your frontend: pale neutral paper, white cards, green only where it matters",
        "#f0f4f1", "#f7faf8", "#fcfefd", "#dfe8e2", "#26382d", "#7c8d82",
        "#eef3ef", "#e6ede8", "#6fb186", "#4a8a60", "#e1efe6", "#ffffff",
    ),
    Style(
        "B-green-horizon", "Style B  Green Horizon", "A gradient band across the top gives the window a strong colour identity",
        "#eef3f0", "#fafcfb", "#ffffff", "#dde7e1", "#22352a", "#75877c",
        "#f3f7f4", "#e7eee9", "#7dbd92", "#3f7f56", "#e4f1e9", "#ffffff",
        glass_header=True,
    ),
    Style(
        "C-night-stage", "Style C  Night Stage", "Light tools around a deep green-black stage, so the slide is the brightest thing on screen",
        "#eef2ef", "#f7faf8", "#fcfefd", "#dbe5df", "#24352b", "#79897f",
        "#1c2a22", "#111a15", "#74b58a", "#468559", "#e0eee5", "#ffffff",
        dark_stage=True,
    ),
]


def sans(size: float, weight=QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "Microsoft YaHei"])
    font.setPixelSize(int(size))
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def numeral(size: float) -> QFont:
    font = QFont("Georgia")
    font.setPixelSize(int(size))
    font.setItalic(True)
    return font


def gradient(style: Style, rect: QRectF, horizontal: bool = True) -> QLinearGradient:
    grad = QLinearGradient(rect.topLeft(), rect.topRight() if horizontal else rect.bottomLeft())
    grad.setColorAt(0, QColor(style.grad_a))
    grad.setColorAt(1, QColor(style.grad_b))
    return grad


def soft_shadow(p: QPainter, rect: QRectF, radius: float, spread: int, alpha: int, dy: int = 8, color=(32, 58, 42)) -> None:
    """A real Gaussian shadow, so depth reads as light falling off rather than a hard edge."""
    pad = spread * 2
    width, height = int(rect.width()) + pad * 2, int(rect.height()) + pad * 2
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, width - pad, height - pad), radius, fill=min(255, alpha * 6))
    mask = mask.filter(ImageFilter.GaussianBlur(spread * 0.55))
    tint = Image.new("RGBA", (width, height), (*color, 0))
    tint.putalpha(mask)
    data = tint.tobytes("raw", "BGRA")
    shadow = QImage(data, width, height, width * 4, QImage.Format.Format_ARGB32).copy()
    p.drawImage(QPointF(rect.left() - pad, rect.top() - pad + dy), shadow)



def panel(p: QPainter, rect: QRectF, fill: str, line: str, radius: float = 14) -> None:
    p.setBrush(QColor(fill))
    p.setPen(QPen(QColor(line), 1))
    p.drawRoundedRect(rect, radius, radius)


def brand_mark(p: QPainter, x: float, y: float, s: float, style: Style, on_dark: bool = False) -> None:
    """Three offset sheets in depth: the James Viewer mark."""
    p.save()
    p.translate(x, y)
    sheets = [(s * 0.34, s * 0.02, 0.35), (s * 0.17, s * 0.13, 0.6), (0, s * 0.24, 1.0)]
    for dx, dy, alpha in sheets:
        rect = QRectF(dx, dy, s * 0.66, s * 0.74)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        a, b = QColor("#ffffff" if on_dark else style.grad_a), QColor("#dff0e5" if on_dark else style.grad_b)
        a.setAlphaF(alpha)
        b.setAlphaF(alpha)
        grad.setColorAt(0, a)
        grad.setColorAt(1, b)
        p.setBrush(grad)
        edge = QColor("#ffffff")
        edge.setAlphaF(0.75)
        p.setPen(QPen(edge, max(1.0, s * 0.03)))
        p.drawRoundedRect(rect, s * 0.1, s * 0.1)
    p.restore()


ICONS = {
    "view": [("rect", 3, 5, 18, 14, 2.5), ("line", 3, 9, 21, 9)],
    "arrange": [("rect", 3, 3, 8, 8, 1.8), ("rect", 13, 3, 8, 8, 1.8), ("rect", 3, 13, 8, 8, 1.8), ("rect", 13, 13, 8, 8, 1.8)],
    "split": [("line", 12, 3, 12, 21), ("line", 7, 8, 3, 12), ("line", 3, 12, 7, 16), ("line", 17, 8, 21, 12), ("line", 21, 12, 17, 16)],
    "lock": [("rect", 5, 11, 14, 10, 2.5), ("line", 8, 11, 8, 7), ("line", 16, 11, 16, 7), ("line", 8, 7, 16, 7)],
    "plus": [("line", 12, 5, 12, 19), ("line", 5, 12, 19, 12)],
    "minus": [("line", 5, 12, 19, 12)],
    "undo": [("line", 4, 10, 16, 10), ("line", 4, 10, 8, 6), ("line", 4, 10, 8, 14), ("line", 16, 10, 20, 14), ("line", 20, 14, 20, 18)],
    "redo": [("line", 20, 10, 8, 10), ("line", 20, 10, 16, 6), ("line", 20, 10, 16, 14), ("line", 8, 10, 4, 14), ("line", 4, 14, 4, 18)],
    "full": [("line", 4, 9, 4, 4), ("line", 4, 4, 9, 4), ("line", 15, 4, 20, 4), ("line", 20, 4, 20, 9), ("line", 20, 15, 20, 20), ("line", 20, 20, 15, 20), ("line", 9, 20, 4, 20), ("line", 4, 20, 4, 15)],
}


def icon(p: QPainter, name: str, x: float, y: float, s: float, color: str, width: float = 1.6) -> None:
    p.save()
    p.translate(x, y)
    p.scale(s / 24, s / 24)
    pen = QPen(QColor(color), width * 24 / s * (s / 24) * 1.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setWidthF(width)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for item in ICONS[name]:
        if item[0] == "rect":
            p.drawRoundedRect(QRectF(item[1], item[2], item[3], item[4]), item[5], item[5])
        else:
            p.drawLine(QPointF(item[1], item[2]), QPointF(item[3], item[4]))
    p.restore()


def sidebar(p: QPainter, style: Style, active: int) -> None:
    rect = QRectF(0, 0, 232, H)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(style.sidebar))
    p.drawRect(rect)
    p.setPen(QPen(QColor(style.panel_line), 1))
    p.drawLine(232, 0, 232, H)
    brand_mark(p, 26, 30, 42, style)
    p.setPen(QColor(style.ink))
    p.setFont(sans(20, QFont.Weight.DemiBold, 0.6))
    p.drawText(QPointF(80, 50), "James Viewer")
    p.setPen(QColor(style.muted))
    p.setFont(sans(9, QFont.Weight.Medium, 1.5))
    p.drawText(QPointF(81, 67), "VIEW · ARRANGE · PRESENT")
    p.setFont(sans(10, QFont.Weight.Medium, 1.6))
    p.drawText(QPointF(30, 124), "WORKSPACE")
    p.setFont(numeral(12))
    p.drawText(QPointF(194, 124), "04")
    items = [("view", "View"), ("arrange", "Arrange"), ("split", "Split & Merge"), ("lock", "Protection")]
    y = 142
    for i, (ico, label) in enumerate(items):
        row = QRectF(18, y, 196, 48)
        if i == active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(style.active_bg))
            p.drawRoundedRect(row, 10, 10)
            bar = QRectF(18, y + 8, 3.5, 32)
            p.setBrush(gradient(style, bar, horizontal=False))
            p.drawRoundedRect(bar, 1.7, 1.7)
        color = style.grad_b if i == active else style.muted
        icon(p, ico, 32, y + 14, 20, color)
        p.setPen(QColor(style.ink if i == active else style.muted))
        p.setFont(sans(14, QFont.Weight.DemiBold if i == active else QFont.Weight.Normal))
        p.drawText(QPointF(64, y + 29), label)
        p.setPen(QColor(style.muted))
        p.setFont(sans(10, QFont.Weight.Normal, 0.8))
        p.drawText(QPointF(186, y + 29), f"0{i + 1}")
        y += 56
    p.setPen(QColor(style.muted))
    p.setFont(sans(12))
    p.drawText(QRectF(30, H - 170, 180, 44), Qt.TextFlag.TextWordWrap, "Your originals stay untouched unless you say otherwise.")
    p.setPen(QPen(QColor(style.panel_line), 1))
    p.drawLine(30, H - 54, 204, H - 54)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawEllipse(QPointF(34, H - 32), 3, 3)
    p.setPen(QColor(style.muted))
    p.setFont(sans(11))
    p.drawText(QPointF(44, H - 28), "Offline")
    p.setFont(sans(10, QFont.Weight.Normal, 1))
    p.drawText(QPointF(170, H - 28), "V 1.0")


def primary_button(p: QPainter, style: Style, rect: QRectF, text: str, icon_name: str | None = None) -> None:
    soft_shadow(p, rect, 8, 10, 10, 4, (60, 120, 80))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient(style, rect))
    p.drawRoundedRect(rect, 8, 8)
    p.setPen(QColor("#ffffff"))
    p.setFont(sans(13, QFont.Weight.DemiBold))
    if icon_name:
        icon(p, icon_name, rect.left() + 12, rect.top() + rect.height() / 2 - 9, 18, "#ffffff")
        p.setPen(QColor("#ffffff"))
        p.drawText(rect.adjusted(36, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
    else:
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def tabs(p: QPainter, style: Style, y: float, on_band: bool) -> None:
    names = [("MySQL函数.pptx", True), ("02_FineBI.pptx", False), ("06-线性回归.pdf", False)]
    x = 256
    for name, active in names:
        width = 188
        rect = QRectF(x, y, width, 38)
        if active:
            soft_shadow(p, rect, 10, 12, 10, 4)
            p.setBrush(QColor(style.tab_bg))
            p.setPen(QPen(QColor(style.panel_line), 1))
            p.drawRoundedRect(rect, 10, 10)
            ink = style.ink
        else:
            if on_band:
                fill = QColor("#ffffff")
                fill.setAlpha(46)
                p.setBrush(fill)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rect, 10, 10)
            ink = "#ffffff" if on_band else style.muted
        p.setPen(QColor(ink))
        p.setFont(sans(13, QFont.Weight.Medium if active else QFont.Weight.Normal))
        p.drawText(rect.adjusted(16, 0, -28, 0), Qt.AlignmentFlag.AlignVCenter, name)
        p.setFont(sans(14))
        p.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "×")
        x += width + 8


def toolbar(p: QPainter, style: Style, y: float, slide_text: str, on_band: bool = False) -> None:
    muted = "#f4fbf6" if on_band else style.muted
    ink = "#ffffff" if on_band else style.ink
    p.setPen(QColor(ink))
    p.setFont(numeral(20))
    p.drawText(QPointF(258, y + 25), slide_text.split("/")[0].strip())
    p.setPen(QColor(muted))
    p.setFont(sans(13))
    p.drawText(QPointF(286, y + 25), "/ " + slide_text.split("/")[1].strip())
    x = 370
    for name in ("minus",):
        icon(p, name, x, y + 10, 20, muted)
    p.setPen(QColor(muted))
    p.setFont(sans(13))
    p.drawText(QRectF(x + 24, y, 56, 40), Qt.AlignmentFlag.AlignCenter, "Fit")
    icon(p, "plus", x + 84, y + 10, 20, muted)
    p.setPen(QPen(QColor(style.panel_line if not on_band else "#66ffffff"), 1))
    p.drawLine(int(x + 118), int(y + 10), int(x + 118), int(y + 30))
    icon(p, "undo", x + 134, y + 10, 20, muted)
    icon(p, "redo", x + 168, y + 10, 20, muted)
    if not on_band:
        primary_button(p, style, QRectF(W - 150, y + 1, 126, 38), "Present  F11", None)
    else:
        rect = QRectF(W - 150, y + 1, 126, 38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(rect, 8, 8)
        p.setPen(QColor(style.grad_b))
        p.setFont(sans(13, QFont.Weight.DemiBold))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Present  F11")


def thumbnail(p: QPainter, style: Style, page: QPixmap, rect: QRectF, number: int, active: bool, dark: bool = False) -> None:
    image_rect = rect.adjusted(26, 0, 0, 0)
    soft_shadow(p, image_rect, 5, 8, 12 if active else 7, 3)
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(image_rect, 5, 5)
    p.setClipPath(clip)
    p.drawPixmap(image_rect.toRect(), page)
    p.restore()
    if active:
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QBrush(gradient(style, image_rect)), 2.4)
        p.setPen(pen)
        p.drawRoundedRect(image_rect.adjusted(-1, -1, 1, 1), 6, 6)
    p.setPen(QColor(style.grad_b if active else style.muted))
    p.setFont(numeral(14))
    p.drawText(QRectF(rect.left(), rect.top(), 22, 24), Qt.AlignmentFlag.AlignCenter, str(number))


def view_screen(style: Style, pages: list[QPixmap], big: QPixmap) -> QImage:
    image = QImage(W, H, QImage.Format.Format_RGB32)
    image.fill(QColor(style.bg))
    p = QPainter(image)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    band_h = 0
    if style.glass_header:
        band_h = 128
        band = QRectF(232, 0, W - 232, band_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient(style, band))
        p.drawRect(band)
        glow = QRadialGradient(QPointF(W - 240, -30), 520)
        glow.setColorAt(0, QColor(255, 255, 255, 66))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(glow)
        p.drawRect(band)
    sidebar(p, style, 0)
    tabs(p, style, 16, style.glass_header)
    toolbar(p, style, 68, "12 / 105", style.glass_header)
    top = 128 if style.glass_header else 118
    if not style.glass_header:
        p.setPen(QPen(QColor(style.panel_line), 1))
        p.drawLine(232, 116, W, 116)
    rail = QRectF(232, top, 214, H - top)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(style.sidebar))
    p.drawRect(rail)
    p.setPen(QPen(QColor(style.panel_line), 1))
    p.drawLine(446, top, 446, H)
    thumb_w = 150
    thumb_h = thumb_w * 9 / 16
    y = top + 22
    for i, page in enumerate(pages[:7]):
        thumbnail(p, style, page, QRectF(248, y, thumb_w + 26, thumb_h), 10 + i, i == 2)
        y += thumb_h + 22
    stage = QRectF(447, top, W - 447, H - top)
    grad = QLinearGradient(stage.topLeft(), stage.bottomLeft())
    grad.setColorAt(0, QColor(style.stage_top))
    grad.setColorAt(1, QColor(style.stage_bottom))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRect(stage)
    if style.dark_stage:
        glow = QRadialGradient(stage.center(), 720)
        glow.setColorAt(0, QColor(92, 157, 114, 70))
        glow.setColorAt(1, QColor(92, 157, 114, 0))
        p.setBrush(glow)
        p.drawRect(stage)
    page_w = stage.width() - 96
    page_h = page_w * 9 / 16
    if page_h > stage.height() - 96:
        page_h = stage.height() - 96
        page_w = page_h * 16 / 9
    page_rect = QRectF(stage.center().x() - page_w / 2, stage.center().y() - page_h / 2, page_w, page_h)
    for offset, alpha in ((22, 90), (11, 150)):
        sheet = page_rect.adjusted(offset, offset, offset, offset)
        fill = QColor("#ffffff" if not style.dark_stage else "#3b4d42")
        fill.setAlpha(alpha)
        p.setBrush(fill)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(sheet, 6, 6)
    soft_shadow(p, page_rect, 6, 34, 14 if not style.dark_stage else 26, 14, (20, 40, 28) if not style.dark_stage else (0, 0, 0))
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(page_rect, 6, 6)
    p.setClipPath(clip)
    p.drawPixmap(page_rect.toRect(), big)
    p.restore()
    p.end()
    return image


def arrange_screen(style: Style, pages: list[QPixmap]) -> QImage:
    image = QImage(W, H, QImage.Format.Format_RGB32)
    image.fill(QColor(style.bg))
    p = QPainter(image)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    if style.glass_header:
        band = QRectF(232, 0, W - 232, 128)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient(style, band))
        p.drawRect(band)
        glow = QRadialGradient(QPointF(W - 240, -30), 520)
        glow.setColorAt(0, QColor(255, 255, 255, 66))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(glow)
        p.drawRect(band)
    sidebar(p, style, 1)
    tabs(p, style, 16, style.glass_header)
    top = 128 if style.glass_header else 76
    if not style.glass_header:
        p.setPen(QPen(QColor(style.panel_line), 1))
        p.drawLine(232, 74, W, 74)
    head_y = top + 26
    p.setPen(QColor(style.muted))
    p.setFont(sans(10, QFont.Weight.DemiBold, 2.2))
    p.drawText(QPointF(264, head_y + 8), "ARRANGE SLIDES")
    p.setPen(QColor(style.ink))
    p.setFont(sans(27, QFont.Weight.DemiBold, 0.4))
    p.drawText(QPointF(264, head_y + 46), "Three selected")
    p.setPen(QColor(style.muted))
    p.setFont(sans(13))
    p.drawText(QPointF(264, head_y + 72), "Drag to reorder. Copy and paste works across open tabs.")
    p.setFont(numeral(58))
    p.setPen(QColor(style.grad_a))
    p.drawText(QRectF(W - 520, head_y - 8, 140, 76), Qt.AlignmentFlag.AlignRight, "03")
    x = W - 360
    for label, width in (("Duplicate", 104), ("Delete", 84)):
        rect = QRectF(x, head_y + 8, width, 38)
        panel(p, rect, style.panel, style.panel_line, 8)
        p.setPen(QColor(style.ink))
        p.setFont(sans(13))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        x += width + 10
    primary_button(p, style, QRectF(x, head_y + 8, 136, 38), "Add slide", "plus")
    grid_top = head_y + 104
    panel_rect = QRectF(264, grid_top, W - 264 - 32, H - grid_top - 28)
    bg_fill = style.panel
    if style.dark_stage:
        grad = QLinearGradient(panel_rect.topLeft(), panel_rect.bottomLeft())
        grad.setColorAt(0, QColor(style.stage_top))
        grad.setColorAt(1, QColor(style.stage_bottom))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(panel_rect, 14, 14)
    else:
        soft_shadow(p, panel_rect, 14, 22, 7, 8)
        panel(p, panel_rect, bg_fill, style.panel_line)
    cols = 4
    gap = 26
    inner = panel_rect.adjusted(30, 28, -30, -22)
    cell_w = (inner.width() - gap * (cols - 1)) / cols
    cell_h = cell_w * 9 / 16
    selected = {1, 2, 5}
    for i, page in enumerate(pages[:12]):
        col, row = i % cols, i // cols
        rect = QRectF(inner.left() + col * (cell_w + gap), inner.top() + row * (cell_h + 46), cell_w, cell_h)
        if rect.bottom() > panel_rect.bottom():
            break
        soft_shadow(p, rect, 7, 12, 12 if i in selected else 8, 5, (0, 0, 0) if style.dark_stage else (32, 58, 42))
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 7, 7)
        p.setClipPath(clip)
        p.drawPixmap(rect.toRect(), page)
        p.restore()
        if i in selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QBrush(gradient(style, rect)), 3))
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 8, 8)
            badge = QRectF(rect.right() - 30, rect.top() + 8, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(gradient(style, badge))
            p.drawEllipse(badge)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(QPointF(badge.left() + 6, badge.top() + 11), QPointF(badge.left() + 10, badge.top() + 15))
            p.drawLine(QPointF(badge.left() + 10, badge.top() + 15), QPointF(badge.left() + 16, badge.top() + 7))
        p.setPen(QColor("#b9d6c4" if style.dark_stage else (style.grad_b if i in selected else style.muted)))
        p.setFont(numeral(15))
        p.drawText(QPointF(rect.left() + 2, rect.bottom() + 24), f"{i + 1:02d}")
    p.end()
    return image


def main(pdf_path: Path, out: Path) -> None:
    app = QGuiApplication(sys.argv)
    out.mkdir(parents=True, exist_ok=True)
    doc = pypdfium2.PdfDocument(str(pdf_path))
    total = len(doc)
    wanted = [i for i in range(total)][:16]

    def pixmap(index: int, width: int) -> QPixmap:
        page = doc[index]
        scale = width / page.get_width()
        bitmap = page.render(scale=scale, rev_byteorder=True).to_pil().convert("RGB")
        data = bitmap.tobytes()
        qimage = QImage(data, bitmap.width, bitmap.height, bitmap.width * 3, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(qimage)

    thumbs = [pixmap(i, 420) for i in wanted]
    big = pixmap(wanted[min(2, len(wanted) - 1)], 1300)
    for style in STYLES:
        view_screen(style, thumbs, big).save(str(out / f"{style.key}-1-viewer.png"))
        arrange_screen(style, thumbs).save(str(out / f"{style.key}-2-arrange.png"))
        print("wrote", style.key)
    del app


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
