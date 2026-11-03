"""The page canvas: fits or zooms a page, floats it on the stage, and selects text."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractScrollArea

from ..core.pdf_engine import PDFIUM_LOCK
from . import theme
from .document import Document

MARGIN = 44
MIN_ZOOM, MAX_ZOOM = 0.1, 8.0


class PageCanvas(QAbstractScrollArea):
    page_requested = Signal(int)
    zoom_changed = Signal(str)
    fullscreen_requested = Signal()
    text_copied = Signal(int)

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        self.mode = "fit"
        self.zoom = 1.0
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._wheel_budget = 0
        self._pan_origin: QPoint | None = None
        self._selection: tuple[int, int] | None = None
        self._rects: list[tuple[float, float, float, float]] = []
        self._text_ref = None
        self._text_handles = None
        self._anchor = -1
        images = document.workspace.images
        images.available.connect(self._image_ready)
        document.renders_changed.connect(self.refresh)
        document.pages_changed.connect(self.refresh)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(60)
        self._debounce.timeout.connect(self.viewport().update)

    # -- geometry ------------------------------------------------------------------------------------

    def _page_points(self) -> tuple[float, float]:
        return self.document.page_size(self.document.current)

    def _scale(self) -> float:
        width, height = self._page_points()
        view = self.viewport().size()
        if self.mode == "fit":
            return max(0.05, min((view.width() - MARGIN * 2) / width, (view.height() - MARGIN * 2) / height))
        if self.mode == "width":
            return max(0.05, (view.width() - MARGIN * 2) / width)
        return self.zoom

    def _page_rect(self) -> QRectF:
        width, height = self._page_points()
        scale = self._scale()
        page_w, page_h = width * scale, height * scale
        view = self.viewport().size()
        x = max(MARGIN, (view.width() - page_w) / 2) - self.horizontalScrollBar().value()
        y = max(MARGIN, (view.height() - page_h) / 2) - self.verticalScrollBar().value()
        return QRectF(round(x), round(y), round(page_w), round(page_h))

    def _update_scrollbars(self) -> None:
        width, height = self._page_points()
        scale = self._scale()
        view = self.viewport().size()
        extra_w = int(width * scale + MARGIN * 2 - view.width())
        extra_h = int(height * scale + MARGIN * 2 - view.height())
        self.horizontalScrollBar().setRange(0, max(0, extra_w))
        self.verticalScrollBar().setRange(0, max(0, extra_h))
        self.horizontalScrollBar().setPageStep(view.width())
        self.verticalScrollBar().setPageStep(view.height())

    def zoom_text(self) -> str:
        return "Fit" if self.mode == "fit" else ("Width" if self.mode == "width" else f"{round(self.zoom * 100)}%")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._after_zoom()

    def set_zoom(self, value: float) -> None:
        self.mode = "custom"
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, value))
        self._after_zoom()

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self._scale() * factor)

    def _after_zoom(self) -> None:
        self._update_scrollbars()
        self.zoom_changed.emit(self.zoom_text())
        self.viewport().update()

    def refresh(self) -> None:
        self._clear_selection()
        self._update_scrollbars()
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scrollbars()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()

    # -- painting ------------------------------------------------------------------------------------

    def _image_ready(self, key) -> None:
        ref = self.document.ref_at(self.document.current) if self.document.pages else None
        if ref is not None and (key[0], key[1]) == (ref.pdf, ref.page):
            self.viewport().update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        view = self.viewport().rect()
        gradient = QLinearGradient(0, 0, 0, view.height())
        gradient.setColorAt(0, QColor(theme.STAGE_TOP))
        gradient.setColorAt(1, QColor(theme.STAGE_BOTTOM))
        painter.fillRect(view, gradient)
        if not self.document.pages:
            self._paint_waiting(painter, QRectF(view).adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN))
            return

        rect = self._page_rect()
        for offset, alpha in ((16, 90), (8, 160)):
            sheet = rect.adjusted(offset, offset, offset, offset)
            color = QColor("#ffffff")
            color.setAlpha(alpha)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(sheet, 5, 5)
        theme.soft_shadow(painter, rect, 5, 20, 0.16, 10)

        clip = QPainterPath()
        clip.addRoundedRect(rect, 5, 5)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor("white"))
        ref = self.document.ref_at(self.document.current)
        if ref is None:
            self._paint_waiting(painter, rect)
        else:
            ratio = self.devicePixelRatioF()
            width_px, height_px = round(rect.width() * ratio), round(rect.height() * ratio)
            pixmap = self.document.workspace.images.get(ref, width_px, height_px, priority=1, on_screen=True)
            if pixmap is None:
                pixmap = self.document.workspace.images.peek_any(ref)
            if pixmap is not None:
                painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
            self._paint_selection(painter, rect, ref)
        painter.restore()

    def _paint_waiting(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QColor(theme.MUTED))
        painter.setFont(theme.sans(13))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Preparing this page…")

    def _paint_selection(self, painter: QPainter, rect: QRectF, ref) -> None:
        if not self._rects:
            return
        scale = rect.width() / max(ref.width, 1)
        color = QColor(theme.ACCENT)
        color.setAlpha(90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for left, bottom, right, top in self._rects:
            painter.drawRect(QRectF(
                rect.left() + left * scale,
                rect.top() + (ref.height - top) * scale,
                (right - left) * scale,
                (top - bottom) * scale,
            ))

    # -- input ---------------------------------------------------------------------------------------

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.1 if delta > 0 else 1 / 1.1)
            return
        bar = self.verticalScrollBar()
        if bar.maximum() > 0:
            at_edge = (delta > 0 and bar.value() == 0) or (delta < 0 and bar.value() == bar.maximum())
            if not at_edge:
                bar.setValue(bar.value() - delta)
                self._wheel_budget = 0
                return
        self._wheel_budget += delta
        if abs(self._wheel_budget) >= 120:
            step = -1 if self._wheel_budget > 0 else 1
            self._wheel_budget = 0
            self.page_requested.emit(self.document.current + step)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        current = self.document.current
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self.page_requested.emit(current + 1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self.page_requested.emit(current - 1)
        elif key == Qt.Key.Key_Home:
            self.page_requested.emit(0)
        elif key == Qt.Key.Key_End:
            self.page_requested.emit(len(self.document.pages) - 1)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_origin = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.LeftButton:
            self._clear_selection()
            self._anchor = self._char_index(event.position())
            self.viewport().update()

    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        if self._pan_origin is not None:
            delta = position.toPoint() - self._pan_origin
            self._pan_origin = position.toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        if event.buttons() & Qt.MouseButton.LeftButton and self._anchor >= 0:
            index = self._char_index(position)
            if index >= 0:
                self._select(min(self._anchor, index), max(self._anchor, index))
            return
        over_page = self._page_rect().contains(position)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor if over_page and self._over_text(position) else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_origin = None
            self.viewport().unsetCursor()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.fullscreen_requested.emit()

    # -- text ----------------------------------------------------------------------------------------

    def _ensure_text(self):
        ref = self.document.ref_at(self.document.current)
        if ref is None:
            return None
        if self._text_ref != ref:
            self._close_text()
            with PDFIUM_LOCK:
                self._text_handles = self.document.workspace.pdfium.text_page(ref.pdf, ref.password, ref.page)
            self._text_ref = ref
        return self._text_handles[1]

    def _close_text(self) -> None:
        if self._text_handles is not None:
            with PDFIUM_LOCK:
                page, text = self._text_handles
                text.close()
                page.close()
        self._text_handles = None
        self._text_ref = None

    def _page_point(self, position: QPointF) -> tuple[float, float] | None:
        ref = self.document.ref_at(self.document.current)
        rect = self._page_rect()
        if ref is None or rect.width() <= 0:
            return None
        scale = rect.width() / max(ref.width, 1)
        return (position.x() - rect.left()) / scale, ref.height - (position.y() - rect.top()) / scale

    def _char_index(self, position: QPointF) -> int:
        point = self._page_point(position)
        text = self._ensure_text()
        if point is None or text is None:
            return -1
        with PDFIUM_LOCK:
            index = text.get_index(point[0], point[1], 12, 12)
        return -1 if index is None else index

    def _over_text(self, position: QPointF) -> bool:
        point = self._page_point(position)
        text = self._ensure_text()
        if point is None or text is None:
            return False
        with PDFIUM_LOCK:
            index = text.get_index(point[0], point[1], 2, 2)
        return index is not None and index >= 0

    def _select(self, start: int, end: int) -> None:
        text = self._ensure_text()
        if text is None:
            return
        count = end - start + 1
        with PDFIUM_LOCK:
            rectangles = text.count_rects(start, count)
            self._rects = [text.get_rect(i) for i in range(rectangles)]
        self._selection = (start, count)
        self.viewport().update()

    def select_all(self) -> None:
        text = self._ensure_text()
        if text is None:
            return
        with PDFIUM_LOCK:
            total = text.count_chars()
        if total > 0:
            self._select(0, total - 1)

    def _clear_selection(self) -> None:
        self._selection = None
        self._rects = []
        self._anchor = -1

    @property
    def has_selection(self) -> bool:
        return self._selection is not None

    def selected_text(self) -> str:
        if self._selection is None:
            return ""
        text = self._ensure_text()
        if text is None:
            return ""
        with PDFIUM_LOCK:
            return text.get_text_range(*self._selection)

    def copy_selection(self) -> bool:
        """Copy selected text. Returns False if there is nothing to copy or the author forbids it."""
        if not self.has_selection or not self.document.can_copy_text:
            return False
        value = self.selected_text().replace("\r\n", "\n")
        QGuiApplication.clipboard().setText(value)
        self.text_copied.emit(len(value))
        return True

    def closeEvent(self, event) -> None:
        self._close_text()
        super().closeEvent(event)


__all__ = ["PageCanvas", "QCursor"]
