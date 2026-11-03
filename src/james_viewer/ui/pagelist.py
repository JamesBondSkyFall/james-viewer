"""Thumbnails: the slim rail beside the canvas and the large Arrange grid share this view."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QMimeData, QModelIndex, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import theme
from .document import Document

MIME = "application/x-james-viewer-pages"
GRID_MIN_THUMB = 200


class PagesModel(QAbstractListModel):
    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        document.pages_changed.connect(self._reset)
        document.renders_changed.connect(self._repaint)
        document.workspace.images.available.connect(self._image_ready)

    def _reset(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def _repaint(self) -> None:
        count = self.rowCount()
        if count:
            self.dataChanged.emit(self.index(0), self.index(count - 1))

    def _image_ready(self, key) -> None:
        if key[2] > 1000:  # full pages, not thumbnails
            return
        self._repaint()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.document.pages)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.ToolTipRole:
            page = self.document.pages[index.row()]
            suffix = " (hidden slide)" if page.hidden else ""
            return f"{self.document.noun.capitalize()} {index.row() + 1}{suffix}"
        return None

    def flags(self, index):
        base = super().flags(index)
        if index.isValid() and self.document.can_edit_pages:
            return base | Qt.ItemFlag.ItemIsDragEnabled
        return base


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, document: Document, width: int, grid: bool, parent=None):
        super().__init__(parent)
        self.document = document
        self.thumb_width = width
        self.grid = grid

    def _grid_ratio(self) -> float:
        """Height over width of a grid cell's image area, taken from the first page."""
        if not self.document.pages:
            return 9 / 16
        width, height = self.document.page_size(0)
        return max(0.5, min(1.45, height / max(width, 1)))

    def _image_rect(self, option_rect: QRect, position: int) -> QRectF:
        page_w, page_h = self.document.page_size(position)
        area_w = self.thumb_width
        area_h = area_w * self._grid_ratio() if self.grid else area_w * page_h / max(page_w, 1)
        scale = min(area_w / page_w, area_h / page_h)
        width, height = page_w * scale, page_h * scale
        if self.grid:
            x = option_rect.left() + (option_rect.width() - area_w) / 2 + (area_w - width) / 2
            y = option_rect.top() + 8 + (area_h - height) / 2
        else:
            x = option_rect.left() + 34
            y = option_rect.top() + 8
        return QRectF(round(x), round(y), round(width), round(height))

    def sizeHint(self, _option, index) -> QSize:
        if self.grid:
            return QSize(self.thumb_width + 30, int(self.thumb_width * self._grid_ratio()) + 44)
        page_w, page_h = self.document.page_size(index.row())
        return QSize(self.thumb_width + 48, int(self.thumb_width * page_h / max(page_w, 1)) + 16)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        position = index.row()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        active = position == self.document.current
        rect = self._image_rect(option.rect, position)

        theme.soft_shadow(painter, rect, 5, 8, 0.14 if selected or active else 0.09, 3)
        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(rect, QColor("white"))
        ref = self.document.ref_at(position)
        if ref is not None:
            ratio = option.widget.devicePixelRatioF() if option.widget else 1.0
            pixmap = self.document.workspace.images.get(ref, round(rect.width() * ratio), round(rect.height() * ratio), priority=6)
            if pixmap is None:
                pixmap = self.document.workspace.images.peek_any(ref)
            if pixmap is not None:
                painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
        page = self.document.pages[position]
        if page.hidden:
            veil = QColor(theme.BG)
            veil.setAlpha(170)
            painter.fillRect(rect, veil)
        painter.restore()

        highlighted = selected or (active and not self.grid)
        if highlighted:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.accent_gradient(rect), 2.4))
            painter.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 6, 6)

        painter.setFont(theme.numeral(14 if not self.grid else 15))
        painter.setPen(QColor(theme.ACCENT_DARK if highlighted else theme.MUTED))
        if self.grid:
            painter.drawText(QPointF(rect.left() + 2, rect.bottom() + 22), f"{position + 1:02d}")
            if selected:
                badge = QRectF(rect.right() - 28, rect.top() + 7, 21, 21)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.accent_gradient(badge))
                painter.drawEllipse(badge)
                pen = QPen(QColor("white"), 2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(badge.left() + 6, badge.top() + 11), QPointF(badge.left() + 9.5, badge.top() + 14.5))
                painter.drawLine(QPointF(badge.left() + 9.5, badge.top() + 14.5), QPointF(badge.left() + 15, badge.top() + 7))
        else:
            painter.drawText(QRectF(option.rect.left() + 6, rect.top(), 24, 22), Qt.AlignmentFlag.AlignCenter, str(position + 1))
        if page.hidden:
            painter.setFont(theme.sans(9, theme.QFont.Weight.DemiBold, 1.0))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "HIDDEN")
        painter.restore()


class PagesView(QListView):
    """Selection, drag reordering and the drop marker. Also the source for Copy."""

    activated_page = Signal(int)
    delete_requested = Signal()

    def __init__(self, document: Document, *, grid: bool, thumb_width: int, parent=None):
        super().__init__(parent)
        self.document = document
        self.grid_mode = grid
        self.setModel(PagesModel(document, self))
        self.setItemDelegate(ThumbDelegate(document, thumb_width, grid, self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if not grid else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(grid)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if grid:
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setResizeMode(QListView.ResizeMode.Adjust)
            self.setMovement(QListView.Movement.Static)
            self.setWrapping(True)
            self.setSpacing(8)
        else:
            self.setViewMode(QListView.ViewMode.ListMode)
            self.setSpacing(3)
        self._drop_row: int | None = None
        self.clicked.connect(lambda index: self.activated_page.emit(index.row()))
        self.verticalScrollBar().valueChanged.connect(lambda _value: self._trim_queue())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.grid_mode:
            self._fill_width()

    def _fill_width(self) -> None:
        """Grow the thumbnails so the columns use the full width, with no empty strip at the right."""
        gap = 30 + self.spacing() * 2
        available = self.viewport().width() - self.spacing() * 2 - 2
        columns = max(2, available // (GRID_MIN_THUMB + gap))
        width = max(GRID_MIN_THUMB, available // columns - gap)
        delegate = self.itemDelegate()
        if delegate.thumb_width != width:
            delegate.thumb_width = width
            self.scheduleDelayedItemsLayout()

    def _trim_queue(self) -> None:
        """Forget thumbnails scrolled out of view, so visible ones render first."""
        self.document.workspace.service.drop_below(6)
        self.viewport().update()

    def selected_positions(self) -> list[int]:
        rows = sorted(index.row() for index in self.selectionModel().selectedIndexes())
        return rows or ([self.document.current] if self.document.pages else [])

    def select_position(self, position: int) -> None:
        if not 0 <= position < self.model().rowCount():
            return
        index = self.model().index(position)
        if position not in [i.row() for i in self.selectionModel().selectedIndexes()] or len(self.selectionModel().selectedIndexes()) == 1:
            self.setCurrentIndex(index)
        self.scrollTo(index)
        self.viewport().update()

    def startDrag(self, _actions) -> None:
        if not self.document.can_edit_pages:
            return
        mime = QMimeData()
        mime.setData(MIME, b"pages")
        drag = QDrag(self)
        drag.setMimeData(mime)
        count = len(self.selected_positions())
        badge = QPixmap(46, 30)
        badge.fill(Qt.GlobalColor.transparent)
        painter = QPainter(badge)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(0, 0, 46, 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.accent_gradient(area))
        painter.drawRoundedRect(area, 8, 8)
        painter.setPen(QColor("white"))
        painter.setFont(theme.numeral(16))
        painter.drawText(area, Qt.AlignmentFlag.AlignCenter, str(count))
        painter.end()
        drag.setPixmap(badge)
        drag.exec(Qt.DropAction.MoveAction)
        self._drop_row = None
        self.viewport().update()

    def _row_for(self, position) -> int:
        index = self.indexAt(position)
        if not index.isValid():
            return self.model().rowCount()
        rect = self.visualRect(index)
        after = position.x() > rect.center().x() if self.grid_mode else position.y() > rect.center().y()
        return index.row() + (1 if after else 0)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME) and event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME) and event.source() is self:
            self._drop_row = self._row_for(event.position().toPoint())
            self.viewport().update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, _event) -> None:
        self._drop_row = None
        self.viewport().update()

    def dropEvent(self, event) -> None:
        if not (event.mimeData().hasFormat(MIME) and event.source() is self):
            event.ignore()
            return
        target = self._row_for(event.position().toPoint())
        self._drop_row = None
        event.acceptProposedAction()
        positions = self.selected_positions()
        self.document.move(positions, target)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_row is None:
            return
        row = self._drop_row
        count = self.model().rowCount()
        if count == 0:
            return
        reference = self.visualRect(self.model().index(min(row, count - 1)))
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.grid_mode:
            x = reference.left() - 2 if row < count else reference.right() + 2
            marker = QRectF(x - 1.5, reference.top() + 6, 3, reference.height() - 12)
            painter.setBrush(theme.accent_gradient(marker, horizontal=False))
        else:
            y = reference.top() - 2 if row < count else reference.bottom() + 2
            marker = QRectF(reference.left() + 34, y - 1.5, reference.width() - 44, 3)
            painter.setBrush(theme.accent_gradient(marker))
        painter.drawRoundedRect(marker, 1.5, 1.5)
        painter.end()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete,):
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)
        index = self.currentIndex()
        if index.isValid() and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.activated_page.emit(index.row())
