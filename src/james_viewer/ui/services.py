"""Application-wide services: background tasks, page rendering and the image cache."""

from __future__ import annotations

import heapq
import itertools
import threading
import traceback
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from ..core.errors import ViewerError
from ..core.office_engine import OfficeEngine
from ..core.paths import Session
from ..core.pdf_engine import PdfiumDocuments

# About 144 MB a page, so one page at high zoom cannot push everything else out of the cache.
MAX_RENDER_PIXELS = 36_000_000


@dataclass(frozen=True)
class RenderRef:
    """Where the pixels for a page live: one page of one PDF file."""

    pdf: str
    page: int
    password: str | None
    width: float
    height: float


class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, function: Callable[[], Any], signals: _TaskSignals):
        super().__init__()
        self.function = function
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.function()
        except ViewerError as exc:
            self.signals.failed.emit(exc)
        except Exception as exc:  # any unexpected failure must reach the user, not vanish in a thread
            traceback.print_exc()
            self.signals.failed.emit(ViewerError(f"Something went wrong: {exc}"))
        else:
            self.signals.done.emit(result)


class ImageService(QThread):
    """Renders pages on one thread: the most urgent first, then the most recently asked for."""

    rendered = Signal(object, QImage)
    failed = Signal(object)

    def __init__(self, pdfium: PdfiumDocuments, parent=None):
        super().__init__(parent)
        self.pdfium = pdfium
        self._heap: list = []
        self._priority: dict[tuple, int] = {}
        self._in_flight: set[tuple] = set()
        self._counter = itertools.count()
        self._wake = threading.Condition()
        self._stopping = False

    def request(self, ref: RenderRef, width_px: int, height_px: int, priority: int) -> None:
        key = (ref.pdf, ref.page, width_px, height_px)
        with self._wake:
            if key in self._in_flight:
                return
            current = self._priority.get(key)
            if current is not None and current <= priority:
                return
            self._priority[key] = priority
            heapq.heappush(self._heap, (priority, -next(self._counter), key, ref))
            self._wake.notify()

    def finished(self, key: tuple) -> None:
        """The result reached the cache, or the render failed, so it may be asked for again."""
        with self._wake:
            self._in_flight.discard(key)

    def drop_below(self, priority_floor: int) -> None:
        """Forget queued work that is less urgent than the floor, such as off-screen thumbnails."""
        with self._wake:
            self._heap = [item for item in self._heap if item[0] < priority_floor]
            heapq.heapify(self._heap)
            self._priority = {item[2]: item[0] for item in self._heap}

    def stop(self) -> None:
        with self._wake:
            self._stopping = True
            self._wake.notify()
        self.wait(5000)

    def run(self) -> None:
        while True:
            with self._wake:
                while not self._heap and not self._stopping:
                    self._wake.wait()
                if self._stopping:
                    return
                priority, _order, key, ref = heapq.heappop(self._heap)
                if self._priority.get(key) != priority:
                    continue
                del self._priority[key]
                self._in_flight.add(key)
            try:
                data, width, height, stride, channels = self.pdfium.render(
                    ref.pdf, ref.password, ref.page, key[2], key[3]
                )
                image_format = QImage.Format.Format_RGB32 if channels == 4 else QImage.Format.Format_BGR888
                image = QImage(data, width, height, stride, image_format).copy()
                self.rendered.emit(key, image)
            except Exception as exc:
                traceback.print_exc()
                self.finished(key)
                self.failed.emit((key, str(exc)))


def _bytes_of(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4


class ImageCache(QObject):
    """Rendered pages as pixmaps, bounded by memory. Used on the GUI thread only."""

    available = Signal(object)

    def __init__(self, service: ImageService, limit_bytes: int = 200 * 1024 * 1024, parent=None):
        super().__init__(parent)
        self.service = service
        self.limit = limit_bytes
        self.used = 0
        self._items: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._on_screen: tuple | None = None
        service.rendered.connect(self._store)

    def key(self, ref: RenderRef, width_px: int, height_px: int) -> tuple:
        pixels = width_px * height_px
        if pixels > MAX_RENDER_PIXELS:
            shrink = (MAX_RENDER_PIXELS / pixels) ** 0.5
            width_px, height_px = int(width_px * shrink), int(height_px * shrink)
        return (ref.pdf, ref.page, width_px, height_px)

    def get(self, ref: RenderRef, width_px: int, height_px: int, priority: int = 5, on_screen: bool = False) -> QPixmap | None:
        """The page at this size, or None after asking for it. The on-screen page is never evicted."""
        key = self.key(ref, width_px, height_px)
        if on_screen:
            self._on_screen = key
        pixmap = self._items.get(key)
        if pixmap is not None:
            self._items.move_to_end(key)
            return pixmap
        self.service.request(ref, key[2], key[3], priority)
        return None

    def peek_any(self, ref: RenderRef) -> QPixmap | None:
        """The sharpest copy of this page held at any size, used until the exact one is ready."""
        best = None
        for key, pixmap in self._items.items():
            if key[0] == ref.pdf and key[1] == ref.page and (best is None or pixmap.width() > best.width()):
                best = pixmap
        return best

    def _store(self, key: tuple, image: QImage) -> None:
        self.service.finished(key)
        pixmap = QPixmap.fromImage(image)
        replaced = self._items.pop(key, None)
        if replaced is not None:
            self.used -= _bytes_of(replaced)
        self._items[key] = pixmap
        self.used += _bytes_of(pixmap)
        for old_key in list(self._items):
            if self.used <= self.limit:
                break
            if old_key not in (key, self._on_screen):
                self.used -= _bytes_of(self._items.pop(old_key))
        self.available.emit(key)


class Workspace(QObject):
    """Everything documents share. Created once, on the GUI thread."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = Session()
        self.office = OfficeEngine()
        self.pdfium = PdfiumDocuments()
        self.renders: dict[tuple, RenderRef] = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.office_pool = QThreadPool(self)
        self.office_pool.setMaxThreadCount(1)
        self.service = ImageService(self.pdfium, self)
        self.images = ImageCache(self.service, parent=self)
        self.page_clipboard: dict | None = None
        self.service.start()
        self._signals: set[_TaskSignals] = set()

    def run(self, function: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
            on_failed: Callable[[ViewerError], None] | None = None, office: bool = False) -> None:
        """Run on a worker thread and call back on the GUI thread."""
        signals = _TaskSignals(self)
        self._signals.add(signals)

        def finish() -> None:
            self._signals.discard(signals)
            signals.deleteLater()

        def done(result: Any) -> None:
            try:
                if on_done:
                    on_done(result)
            finally:
                finish()

        def failed(error: ViewerError) -> None:
            try:
                if on_failed:
                    on_failed(error)
            finally:
                finish()

        signals.done.connect(done)
        signals.failed.connect(failed)
        (self.office_pool if office else self.pool).start(_Task(function, signals))

    def shutdown(self) -> None:
        self.service.stop()
        self.pool.waitForDone(3000)
        self.office.close()
        self.office_pool.waitForDone(3000)
        self.pdfium.close_all()
        self.session.close()


__all__ = ["ImageCache", "ImageService", "RenderRef", "Workspace", "QPixmap"]
