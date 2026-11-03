"""Reading Markdown: a paper column, an outline panel, wheel zoom and true full screen."""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QImage, QImageReader, QLinearGradient, QPainter,
    QTextCursor,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget,
)

from ..core.markdown_engine import Asset, MarkdownPage
from ..core.pdf_engine import PdfAccess, copy_file
from . import theme, widgets
from .presenter import CursorAutoHide

BASE_FONT = 16.0
READING_WIDTH = 900
MIN_ZOOM, MAX_ZOOM = 0.5, 4.0
ZOOM_PER_NOTCH = 1.1
NESTING_INDENT = 44  # the width each surrounding quote or list takes from an image's column
_ASSET_MARK = re.compile("⟦(\\d+)⟧")
_ASSET_URL = re.compile(r"asset:(\d+)/(\d+)/(\d+)")
# Heading level: font scale, space above, space below (all in ems), and colour.
_HEADING_STYLE = {
    1: (2.0, 0.6, 0.9, theme.INK),
    2: (1.6, 1.5, 0.7, "#2f5a41"),
    3: (1.36, 1.3, 0.55, theme.INK),
    4: (1.2, 1.1, 0.45, theme.INK),
    5: (1.08, 0.95, 0.4, theme.INK),
    6: (1.0, 0.9, 0.4, theme.MUTED),
}


class MarkdownDocument(QObject):
    """The same face a Document shows to the main window. There is nothing to edit or render."""

    state_changed = Signal()
    failed = Signal(str)
    status = Signal(str)
    renders_changed = Signal()
    pages_changed = Signal()

    def __init__(self, page: MarkdownPage, parent=None):
        super().__init__(parent)
        self.page = page
        self.path = page.path
        self.name = page.path.name
        self.format = "md"
        self.family = "markdown"
        self.noun = "page"
        self.protected = False
        self.access = PdfAccess()
        self.deck = None
        self.notes = list(page.notes)
        self.pages = (page,)
        self.dirty = False
        self.preparing = False
        self.current = 0
        self.undo_label = ""
        self.redo_label = ""
        self.can_edit_pages = False
        self.can_copy_text = True
        self.edit_block_reason = "Markdown files are read-only in James Viewer."
        self.closed = False

    def start_rendering(self) -> None:
        pass

    def add_font_notes(self) -> None:
        pass

    def save_copy(self, out: Path, *, unprotected: bool = False) -> list[str]:
        copy_file(self.path, out)
        return []

    def mark_saved(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _Browser(QTextBrowser):
    """Reading keys and the wheel. Images are drawn on demand, so zooming a long file stays quick."""

    zoom_step = Signal(float, QPoint)
    pointer_moved = Signal()

    def __init__(self, reader: "MarkdownReader", parent=None):
        super().__init__(parent)
        self.reader = reader
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def loadResource(self, kind, url):  # noqa: N802 - Qt's own name
        if url.scheme() == "asset":
            return self.reader.asset_image(url)
        return super().loadResource(kind, url)

    def wheelEvent(self, event) -> None:
        if self.reader.wheel_zooms or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                # One mouse-wheel notch is 120 units. Touchpads send many small steps, which add up the same.
                self.zoom_step.emit(ZOOM_PER_NOTCH ** (delta / 120), event.position().toPoint())
            event.accept()
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self.pointer_moved.emit()
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        key = event.key()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Down:
            bar.setValue(bar.value() + 60)
        elif key == Qt.Key.Key_Up:
            bar.setValue(bar.value() - 60)
        elif key in (Qt.Key.Key_PageDown, Qt.Key.Key_Space, Qt.Key.Key_Right):
            bar.setValue(bar.value() + int(self.viewport().height() * 0.9))
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_Backspace, Qt.Key.Key_Left):
            bar.setValue(bar.value() - int(self.viewport().height() * 0.9))
        elif key == Qt.Key.Key_Home:
            bar.setValue(0)
        elif key == Qt.Key.Key_End:
            bar.setValue(bar.maximum())
        else:
            super().keyPressEvent(event)


class MarkdownReader(QWidget):
    """The document itself. Its zoom, scroll position and rendering are self-contained."""

    zoom_changed = Signal(str)
    scrolled = Signal()
    link_opened = Signal(str)
    pointer_moved = Signal()

    def __init__(self, page: MarkdownPage, *, full_screen: bool = False, parent=None):
        super().__init__(parent)
        self.page = page
        self.full_screen = full_screen
        self.zoom = 1.0
        self.wheel_zooms = False
        self._anchor: tuple[int, int] | None = None
        self._held: tuple[int, int] | None = None
        self._held_scroll = -1
        self.browser = _Browser(self, self)
        self.browser.zoom_step.connect(self._wheel_zoom)
        self.browser.anchorClicked.connect(self._link_clicked)
        self.browser.verticalScrollBar().valueChanged.connect(lambda _value: self.scrolled.emit())
        self.browser.pointer_moved.connect(self.pointer_moved)
        if full_screen:
            self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rendered_width = -1
        self._heading_tops: list[tuple[int, str]] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self.render)
        self.selection_available = False
        self.browser.copyAvailable.connect(self._note_selection)
        self._fill_natural_sizes()

    def _note_selection(self, available: bool) -> None:
        self.selection_available = available

    # -- the same zoom face the page canvas has ------------------------------------------------------

    def zoom_text(self) -> str:
        return f"{round(self.zoom * 100)}%"

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.zoom * factor)

    def _wheel_zoom(self, factor: float, point: QPoint) -> None:
        self._remember_anchor(point)
        self.zoom_by(factor)

    def _remember_anchor(self, point: QPoint | None = None) -> None:
        """Note the text at this spot, so it stays in place on screen through the zoom."""
        if self._anchor is not None:
            return  # still the same zoom gesture, so keep its original spot
        viewport = self.browser.viewport()
        if point is None or not viewport.rect().contains(point):
            point = viewport.rect().center()
        # Until the page is scrolled, keep holding the same character, so that zooming back returns
        # to exactly the same view even though lines re-wrap at each size in between.
        if self._held is not None and self._held_scroll == self.browser.verticalScrollBar().value():
            self._anchor = self._held
            return
        # Keep the text where it is now, not at the pointer's height: over an image or blank space
        # the nearest text can be far from the pointer, and the page would jump on every step.
        cursor = self.browser.cursorForPosition(point)
        self._anchor = (cursor.position(), self.browser.cursorRect(cursor).top())

    def set_zoom(self, value: float) -> None:
        value = max(MIN_ZOOM, min(MAX_ZOOM, value))
        if abs(value - self.zoom) < 0.001:
            return
        self._remember_anchor()
        self.zoom = value
        self.zoom_changed.emit(self.zoom_text())
        self._timer.start()

    def set_mode(self, _mode: str) -> None:
        self.set_zoom(1.0)

    @property
    def has_selection(self) -> bool:
        return self.browser.textCursor().hasSelection()

    def copy_selection(self) -> bool:
        if not self.has_selection:
            return False
        self.browser.copy()
        return True

    def select_all(self) -> None:
        self.browser.selectAll()

    def setFocus(self) -> None:  # noqa: N802 - Qt's own name
        self.browser.setFocus()

    # -- geometry ------------------------------------------------------------------------------------

    def _paper_rect(self) -> QRectF:
        width = self.width()
        if self.full_screen:
            column = min(width, READING_WIDTH * max(self.zoom, 1.0) + 120)
            return QRectF((width - column) / 2, 0, column, self.height())
        column = min(width - 56, READING_WIDTH * max(self.zoom, 1.0) + 100)
        return QRectF((width - column) / 2, 18, column, self.height() - 18)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_browser()

    def _place_browser_margins(self) -> None:
        paper = self._paper_rect()
        side = int(paper.left() + 28)
        right = max(0, self.width() - int(paper.right()) + 28)
        self.browser.setViewportMargins(side, int(paper.top()), right, 0)

    def _place_browser(self) -> None:
        self.browser.setGeometry(self.rect())
        self._place_browser_margins()
        if self._content_width() != self._rendered_width:
            self._timer.start()
        self.update()

    def _content_width(self) -> int:
        return max(200, self.browser.viewport().width())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.full_screen:
            painter.fillRect(self.rect(), QColor("white"))
            return
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(theme.STAGE_TOP))
        gradient.setColorAt(1, QColor(theme.STAGE_BOTTOM))
        painter.fillRect(self.rect(), gradient)
        paper = self._paper_rect()
        tall = paper.adjusted(0, 0, 0, 40)  # runs past the bottom, like a long sheet
        theme.soft_shadow(painter, tall, 6, 16, 0.12, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(tall, 6, 6)

    def wheelEvent(self, event) -> None:
        """Wheel turns over the margins beside the paper act on the document too."""
        self.browser.wheelEvent(event)

    # -- rendering -----------------------------------------------------------------------------------

    def _fill_natural_sizes(self) -> None:
        for asset in self.page.assets:
            if asset.kind != "image" or asset.width and asset.height:
                continue
            reader = QImageReader()
            if asset.blob is not None:
                image = QImage.fromData(asset.blob)
                size = image.size()
            else:
                reader.setFileName(asset.source)
                size = reader.size()
            if size.width() <= 0 or size.height() <= 0:
                asset.error = "cannot be read"
                continue
            natural_w, natural_h = size.width(), size.height()
            if asset.width and not asset.height:
                asset.height = natural_h * asset.width / natural_w
            elif asset.height and not asset.width:
                asset.width = natural_w * asset.height / natural_h
            else:
                asset.width, asset.height = natural_w, natural_h  # one image pixel per CSS pixel, like Typora

    def asset_image(self, url: QUrl) -> QImage:
        """Draw an image or formula at the exact size asked for. Called only as it comes into view."""
        found = _ASSET_URL.fullmatch(url.toString())
        if found is None:
            return QImage()
        asset = self.page.assets[int(found.group(1))]
        pixel_w, pixel_h = int(found.group(2)), int(found.group(3))
        if asset.kind == "image":
            source = QImage.fromData(asset.blob) if asset.blob is not None else QImage(asset.source)
            if source.isNull():
                return QImage()
            if source.width() == pixel_w and source.height() == pixel_h:
                return source
            return source.scaled(pixel_w, pixel_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        image = QImage(pixel_w, pixel_h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = asset.height + asset.pad_top + asset.pad_bottom
        scale = pixel_h / total
        QSvgRenderer(asset.blob).render(painter, QRectF(0, asset.pad_top * scale, pixel_w, asset.height * scale))
        painter.end()
        return image

    def _expand(self, match: re.Match, ratio: float, limit: float) -> str:
        asset = self.page.assets[int(match.group(1))]
        if asset.error or (asset.kind == "math" and asset.blob is None):
            if asset.kind == "math":
                return f'<span class="inline-code">&nbsp;{html.escape(asset.source)}&nbsp;</span>'
            return f'<span style="color:#9a6a3c;">[{html.escape(asset.alt or Path(asset.source).name)}: {html.escape(asset.error)}]</span>'
        natural_height = asset.height + asset.pad_top + asset.pad_bottom
        width, height = asset.width * asset.zoom * self.zoom, natural_height * asset.zoom * self.zoom
        limit = max(80.0, limit - asset.depth * NESTING_INDENT * self.zoom)
        if width > limit:
            height *= limit / width
            width = limit
        align = ' style="vertical-align: middle;"' if asset.kind == "math" else ""
        pixel_w, pixel_h = max(1, round(width * ratio)), max(1, round(height * ratio))
        return f'<img src="asset:{asset.key}/{pixel_w}/{pixel_h}" width="{round(width)}" height="{round(height)}"{align}>'

    def _style_sheet(self, zoom: float) -> str:
        size = BASE_FONT * zoom

        def px(multiple: float) -> str:
            return f"{round(size * multiple)}px"

        return f"""
        body {{ font-family: 'Segoe UI', 'Microsoft YaHei'; font-size: {px(1)}; color: {theme.INK}; }}
        p, li {{ line-height: 122%; }}
        p {{ margin-top: {px(0.5)}; margin-bottom: {px(0.7)}; }}
        p.gap {{ margin: 0; font-size: {px(0.5)}; line-height: 100%; }}
        ul, ol {{ margin-top: {px(0.25)}; margin-bottom: {px(0.5)}; }}
        a {{ color: {theme.ACCENT_DARK}; text-decoration: none; }}
        pre.code {{ font-family: Consolas, 'Microsoft YaHei'; font-size: {px(0.86)}; white-space: pre-wrap; margin: 0; line-height: 140%; color: #2b3a31; }}
        .inline-code {{ font-family: Consolas, 'Microsoft YaHei'; font-size: {px(0.9)}; background-color: #eef3ef; color: #2f5a41; }}
        hr {{ margin: {px(0.6)} 0; color: {theme.LINE}; }}
        """

    def render(self) -> None:
        self._place_browser_margins()
        bar = self.browser.verticalScrollBar()
        fraction = bar.value() / bar.maximum() if bar.maximum() > 0 else 0.0
        anchor, self._anchor = self._anchor, None
        document = self.browser.document()
        ratio = self.devicePixelRatioF()
        width = self._content_width()
        self._rendered_width = width
        margin = max(12, 28 * self.zoom)
        limit = max(120.0, width - margin * 2 - 8)
        body = _ASSET_MARK.sub(lambda match: self._expand(match, ratio, limit), self.page.template)
        pad = str(round(13 * self.zoom))
        body = (
            body.replace("%%CODEPAD%%", pad)
            .replace("%%TABLEPAD%%", str(round(8 * self.zoom)))
            .replace("%%QUOTEPAD%%", pad)
        )
        size = BASE_FONT * self.zoom
        for level, (scale, above, below, color) in _HEADING_STYLE.items():
            body = body.replace(
                f"%%H{level}%%",
                f"font-size:{round(size * scale)}px; font-weight:600; color:{color}; "
                f"margin-top:{round(size * above)}px; margin-bottom:{round(size * below)}px;",
            )
        font = QFont()
        font.setFamilies(theme.SANS_FAMILIES)
        self.browser.setUpdatesEnabled(False)
        try:
            document.setDefaultFont(font)
            document.setDocumentMargin(margin)
            self.browser.setHtml(f"<html><head><style>{self._style_sheet(self.zoom)}</style></head><body>{body}</body></html>")
            document.size()  # finishes the layout, so the scroll range below is the real one
            if anchor is not None:
                cursor = QTextCursor(document)
                cursor.setPosition(min(anchor[0], document.characterCount() - 1))
                bar.setValue(bar.value() + self.browser.cursorRect(cursor).top() - anchor[1])
                self._held, self._held_scroll = anchor, bar.value()
            elif bar.maximum() > 0:
                bar.setValue(round(fraction * bar.maximum()))
        finally:
            self.browser.setUpdatesEnabled(True)
        self._heading_tops = []
        QTimer.singleShot(0, self._index_headings)
        self.update()

    def _index_headings(self) -> None:
        """Remember where each heading landed, for highlighting the current one in the outline."""
        document = self.browser.document()
        layout = document.documentLayout()
        tops = []
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    for name in fragment.charFormat().anchorNames():
                        if re.fullmatch(r"h\d+", name):
                            tops.append((round(layout.blockBoundingRect(block).top()), name))
                iterator += 1
            block = block.next()
        self._heading_tops = tops
        self.scrolled.emit()

    def current_heading(self) -> str | None:
        bar = self.browser.verticalScrollBar()
        if self._heading_tops and bar.maximum() > 0 and bar.value() >= bar.maximum() - 2:
            return self._heading_tops[-1][1]  # the last headings can never reach the top of the view
        position = bar.value() + 30
        current = None
        for top, name in self._heading_tops:
            if top > position:
                break
            current = name
        return current

    def go_to_heading(self, anchor: str) -> None:
        self.browser.scrollToAnchor(anchor)

    def scroll_fraction(self) -> float:
        bar = self.browser.verticalScrollBar()
        return bar.value() / bar.maximum() if bar.maximum() > 0 else 0.0

    def set_scroll_fraction(self, fraction: float) -> None:
        bar = self.browser.verticalScrollBar()
        bar.setValue(round(fraction * bar.maximum()))

    def _link_clicked(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        if url.hasFragment() and not url.path():
            self.browser.scrollToAnchor(url.fragment())
            return
        target = (self.page.path.parent / url.toString(QUrl.UrlFormattingOption.RemoveFragment)).resolve()
        if target.suffix.lower() in (".md", ".markdown", ".pdf", ".ppt", ".pptx") and target.is_file():
            self.link_opened.emit(str(target))


class Outline(QFrame):
    """The document's headings. Clicking one jumps to it, and the current one stays marked."""

    chosen = Signal(str)

    def __init__(self, page: MarkdownPage, parent=None):
        super().__init__(parent)
        self.setObjectName("Rail")
        self.setFixedWidth(264)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 6, 8)
        layout.setSpacing(8)
        layout.addWidget(widgets.eyebrow("Document layout"))
        self.items = QListWidget()
        self.items.setFrameShape(QFrame.Shape.NoFrame)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.setStyleSheet(
            f"QListWidget {{ background: transparent; outline: none; }}"
            f"QListWidget::item {{ padding: 5px 6px; border-radius: 6px; color: {theme.MUTED}; }}"
            f"QListWidget::item:hover {{ background: {theme.ACCENT_FAINT}; }}"
            f"QListWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.INK}; }}"
        )
        self.items.setTextElideMode(Qt.TextElideMode.ElideRight)
        top = min((heading.level for heading in page.headings), default=1)
        self._rows: dict[str, int] = {}
        for row, heading in enumerate(page.headings):
            depth = heading.level - top
            item = QListWidgetItem("    " * depth + heading.text)
            item.setData(Qt.ItemDataRole.UserRole, heading.anchor)
            item.setToolTip(heading.text)
            font = theme.sans(13 if depth > 0 else 13, theme.QFont.Weight.DemiBold if depth == 0 else theme.QFont.Weight.Normal)
            item.setFont(font)
            self.items.addItem(item)
            self._rows[heading.anchor] = row
        self.items.itemClicked.connect(lambda item: self.chosen.emit(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.items, 1)
        if not page.headings:
            layout.addWidget(widgets.body("This document has no headings.", 12))

    def mark(self, anchor: str | None) -> None:
        row = self._rows.get(anchor or "", -1)
        if row >= 0 and row != self.items.currentRow():
            self.items.blockSignals(True)
            self.items.setCurrentRow(row)
            self.items.blockSignals(False)
            self.items.scrollToItem(self.items.item(row))


class MarkdownTab(QWidget):
    """A Markdown document's tab in the main window."""

    message = Signal(str)
    present_requested = Signal()
    save_requested = Signal()
    details_requested = Signal()
    insert_file_requested = Signal()
    paste_requested = Signal()
    link_opened = Signal(str)

    def __init__(self, document: MarkdownDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self.mode = "view"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_toolbar())
        self.banner = widgets.Banner()
        self.banner.details_requested.connect(self.details_requested)
        outer.addWidget(self.banner)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.outline = Outline(document.page)
        self.canvas = MarkdownReader(document.page)
        self.outline.chosen.connect(self.canvas.go_to_heading)
        self.canvas.scrolled.connect(lambda: self.outline.mark(self.canvas.current_heading()))
        self.canvas.zoom_changed.connect(self.zoom_label.setText)
        self.canvas.link_opened.connect(self.link_opened)
        row.addWidget(self.outline)
        row.addWidget(self.canvas, 1)
        outer.addLayout(row, 1)
        self.zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.1))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.1))
        self.zoom_label.clicked.connect(lambda: self.canvas.set_zoom(1.0))

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Toolbar")
        bar.setFixedHeight(58)
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(4)
        self.layout_toggle = widgets.tool_button("arrange", "Show or hide the document layout (Ctrl+L)", checkable=True)
        self.layout_toggle.setChecked(True)
        self.layout_toggle.clicked.connect(self._layout_clicked)
        row.addWidget(self.layout_toggle)
        row.addSpacing(10)
        divider = QFrame()
        divider.setFixedSize(1, 22)
        divider.setStyleSheet(f"background: {theme.LINE_STRONG};")
        row.addWidget(divider)
        row.addSpacing(10)
        self.zoom_out = widgets.tool_button("minus", "Zoom out (Ctrl + wheel down)")
        self.zoom_label = QToolButton()
        self.zoom_label.setText("100%")
        self.zoom_label.setFixedWidth(62)
        self.zoom_label.setToolTip("Back to 100% (Ctrl+0)")
        self.zoom_in = widgets.tool_button("plus", "Zoom in (Ctrl + wheel up)")
        row.addWidget(self.zoom_out)
        row.addWidget(self.zoom_label)
        row.addWidget(self.zoom_in)
        row.addSpacing(14)
        self.wheel_toggle = QToolButton()
        self.wheel_toggle.setCheckable(True)
        self.wheel_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wheel_toggle.setText("Wheel: Scroll")
        self.wheel_toggle.setToolTip("Choose what the plain mouse wheel does. Ctrl + wheel always zooms.")
        self.wheel_toggle.toggled.connect(self._wheel_mode_changed)
        row.addWidget(self.wheel_toggle)
        row.addStretch(1)
        save = QPushButton("Save As")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self.save_requested)
        row.addWidget(save)
        row.addSpacing(8)
        present = widgets.primary_button("Full Screen  F11")
        present.clicked.connect(self.present_requested)
        row.addWidget(present)
        return bar

    def _layout_clicked(self, checked: bool) -> None:
        self.outline.setVisible(checked)

    def toggle_layout(self) -> None:
        self.layout_toggle.setChecked(not self.layout_toggle.isChecked())
        self._layout_clicked(self.layout_toggle.isChecked())

    def _wheel_mode_changed(self, zooms: bool) -> None:
        self.canvas.wheel_zooms = zooms
        self.wheel_toggle.setText("Wheel: Zoom" if zooms else "Wheel: Scroll")

    # -- what the main window calls on any tab -----------------------------------------------------------

    def set_mode(self, _mode: str) -> None:
        self.mode = "view"

    def focus_page(self) -> None:
        self.canvas.setFocus()

    def go_to(self, _position: int) -> None:
        pass

    def _selection(self) -> list[int]:
        return []

    def undo(self) -> None:
        pass

    def redo(self) -> None:
        pass

    def duplicate_selected(self) -> None:
        pass

    def copy_pages(self) -> None:
        if not self.canvas.copy_selection():
            self.message.emit("Select some text first.")

    def paste_pages(self) -> None:
        pass


class MarkdownPresenter(QWidget):
    """Markdown in true full screen: only the document, until Esc or F11."""

    finished = Signal(float, float)

    def __init__(self, document: MarkdownDocument, zoom: float, fraction: float, wheel_zooms: bool = False, show_layout: bool = False):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{document.name} - James Viewer")
        self.document = document
        self._fraction = fraction
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.outline = Outline(document.page)
        self.outline.setVisible(show_layout)
        self.reader = MarkdownReader(document.page, full_screen=True)
        self.reader.zoom = zoom
        self.reader.wheel_zooms = wheel_zooms
        self.outline.chosen.connect(self.reader.go_to_heading)
        self.reader.scrolled.connect(lambda: self.outline.mark(self.reader.current_heading()))
        self.reader.pointer_moved.connect(self._pointer_moved)
        row.addWidget(self.outline)
        row.addWidget(self.reader, 1)
        self.setMouseTracking(True)
        self.cursor_hider = CursorAutoHide(self)
        self.reader.browser.installEventFilter(self)

    def present(self, screen) -> None:
        self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.reader.render()
        self.reader.set_scroll_fraction(self._fraction)
        self.reader.browser.setFocus()
        self.cursor_hider.hide()

    def toggle_layout(self) -> None:
        self.outline.setVisible(not self.outline.isVisible())

    def eventFilter(self, watched, event) -> bool:
        if event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
                self.close()
                return True
            if key == Qt.Key.Key_L and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.toggle_layout()
                return True
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_0:
                    self.reader.set_zoom(1.0)
                    return True
                if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    self.reader.zoom_by(ZOOM_PER_NOTCH)
                    return True
                if key == Qt.Key.Key_Minus:
                    self.reader.zoom_by(1 / ZOOM_PER_NOTCH)
                    return True
        return super().eventFilter(watched, event)

    def _pointer_moved(self) -> None:
        self.cursor_hider.pointer_moved()

    def closeEvent(self, event) -> None:
        self.cursor_hider.release()
        self.finished.emit(self.reader.zoom, self.reader.scroll_fraction())
        super().closeEvent(event)


__all__ = ["MarkdownDocument", "MarkdownPresenter", "MarkdownTab", "QGuiApplication", "QSize"]
