"""True full-screen: a borderless window holding nothing but the current page on black."""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from .document import Document

_ES_CONTINUOUS = 0x80000000
_ES_DISPLAY_REQUIRED = 0x00000002
CURSOR_IDLE_MS = 1500


class CursorAutoHide(QObject):
    """Hides the pointer at once, and again after it rests. Used by both full-screen windows.

    A widget's cursor only takes effect once the mouse moves, so entering full screen with a still
    mouse would leave the pointer showing. The application-wide override applies immediately.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hidden = False
        self._last_position = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(CURSOR_IDLE_MS)
        self._timer.timeout.connect(self.hide)

    def hide(self) -> None:
        self._last_position = QCursor.pos()
        if not self.hidden:
            QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
            self.hidden = True

    def pointer_moved(self) -> None:
        # Showing a window sends a mouse-move event even when the mouse has not moved.
        if QCursor.pos() == self._last_position:
            return
        self._last_position = QCursor.pos()
        if self.hidden:
            QGuiApplication.restoreOverrideCursor()
            self.hidden = False
        self._timer.start()

    def release(self) -> None:
        self._timer.stop()
        if self.hidden:
            QGuiApplication.restoreOverrideCursor()
            self.hidden = False


class Presenter(QWidget):
    finished = Signal(int)

    def __init__(self, document: Document):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.document = document
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowTitle(f"{document.name} - James Viewer")
        self.position = document.current
        self._shown_pixmap = None
        self._wheel_budget = 0
        self.cursor_hider = CursorAutoHide(self)
        document.workspace.images.available.connect(self._image_ready)
        document.renders_changed.connect(self.update)

    def present(self, screen) -> None:
        """Enter full-screen on the monitor holding the main window."""
        self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.cursor_hider.hide()
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_DISPLAY_REQUIRED)
        self._prepare_neighbours()

    # -- navigation ----------------------------------------------------------------------------------

    def _step(self, direction: int) -> None:
        """Move to the next page in that direction, skipping slides marked hidden."""
        pages = self.document.pages
        position = self.position + direction
        while 0 <= position < len(pages) and pages[position].hidden:
            position += direction
        if 0 <= position < len(pages):
            self._go(position)

    def _go(self, position: int) -> None:
        self.position = position
        self.update()
        self._prepare_neighbours()

    def _pixel_size(self, position: int) -> tuple[int, int] | None:
        width, height = self.document.page_size(position)
        if width <= 0 or height <= 0:
            return None
        ratio = self.devicePixelRatioF()
        screen_w, screen_h = self.width() * ratio, self.height() * ratio
        scale = min(screen_w / width, screen_h / height)
        return round(width * scale), round(height * scale)

    def _prepare_neighbours(self) -> None:
        for offset, priority in ((1, 1), (-1, 2), (2, 3)):
            position = self.position + offset
            ref = self.document.ref_at(position)
            size = self._pixel_size(position) if ref is not None else None
            if size is not None:
                self.document.workspace.images.get(ref, size[0], size[1], priority=priority)

    # -- painting ------------------------------------------------------------------------------------

    def _image_ready(self, key) -> None:
        ref = self.document.ref_at(self.position)
        if ref is not None and key[0] == ref.pdf and key[1] == ref.page:
            self.update()

    def page_rect(self) -> QRectF:
        """The page's rectangle on screen: fitted, centred, and never cropped or stretched."""
        width, height = self.document.page_size(self.position)
        scale = min(self.width() / width, self.height() / height)
        page_w, page_h = width * scale, height * scale
        return QRectF((self.width() - page_w) / 2, (self.height() - page_h) / 2, page_w, page_h)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ref = self.document.ref_at(self.position)
        pixmap = None
        if ref is not None:
            size = self._pixel_size(self.position)
            pixmap = self.document.workspace.images.get(ref, size[0], size[1], priority=0, on_screen=True)
            if pixmap is None:
                pixmap = self.document.workspace.images.peek_any(ref)
        if pixmap is not None:
            self._shown_pixmap = pixmap
            painter.drawPixmap(self.page_rect(), pixmap, QRectF(pixmap.rect()))
        elif self._shown_pixmap is not None:
            # Never flash black between pages. Hold the last page until the next one is ready.
            painter.drawPixmap(self.page_rect(), self._shown_pixmap, QRectF(self._shown_pixmap.rect()))

    # -- input ---------------------------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            self.close()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._step(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self._step(-1)
        elif key == Qt.Key.Key_Home:
            self._go(0)
        elif key == Qt.Key.Key_End:
            self._go(len(self.document.pages) - 1)

    def wheelEvent(self, event) -> None:
        self._wheel_budget += event.angleDelta().y()
        if abs(self._wheel_budget) >= 120:
            self._step(-1 if self._wheel_budget > 0 else 1)
            self._wheel_budget = 0

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._step(1)
        elif event.button() == Qt.MouseButton.RightButton:
            self._step(-1)

    def mouseMoveEvent(self, _event) -> None:
        self.cursor_hider.pointer_moved()

    def closeEvent(self, event) -> None:
        self.cursor_hider.release()
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        self.document.workspace.images.available.disconnect(self._image_ready)
        self.finished.emit(self.position)
        super().closeEvent(event)


__all__ = ["Presenter", "QGuiApplication"]
