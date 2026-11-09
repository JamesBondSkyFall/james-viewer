"""Reading Markdown: a paper column, an outline panel, wheel zoom and true full screen."""

from __future__ import annotations

import html
import re
from pathlib import Path

import shiboken6

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QImage, QImageReader, QLinearGradient, QPainter,
    QTextCursor,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from ..core.markdown_engine import Asset, MarkdownPage, parse_markdown_text, source_encoding
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
        self.preparing = False
        self.current = 0
        self.can_edit_pages = False
        self.can_copy_text = True
        self.edit_block_reason = "Markdown has no pages to arrange. Use Edit to change the text."
        self.closed = False
        self.replace_confirmed = False
        self.text = page.text
        self._saved_text = page.text
        self._history: list[tuple[str, str]] = []
        self._future: list[tuple[str, str]] = []
        try:
            raw = self.path.read_bytes()
        except OSError:
            raw = b""
        self.encoding = source_encoding(raw)
        self.newline = "\r\n" if b"\r\n" in raw else "\n"

    text_changed = Signal()

    @property
    def dirty(self) -> bool:
        return self.text != self._saved_text

    @property
    def undo_label(self) -> str:
        return self._history[-1][0] if self._history else ""

    @property
    def redo_label(self) -> str:
        return self._future[-1][0] if self._future else ""

    def _set_text(self, text: str) -> None:
        self.text = text
        self.page = parse_markdown_text(self.path, text)
        self.pages = (self.page,)
        self.text_changed.emit()
        self.state_changed.emit()

    def replace_lines(self, start: int, end: int, text: str) -> None:
        """Put `text` in place of source lines start to end, the Markdown behind one block."""
        lines = self.text.split("\n")
        new = lines[:start] + (text.split("\n") if text else []) + lines[end:]
        updated = "\n".join(new)
        if updated == self.text:
            return
        self._history.append(("Edit text", self.text))
        self._future.clear()
        self._set_text(updated)

    def undo(self) -> None:
        if self._history:
            label, text = self._history.pop()
            self._future.append((label, self.text))
            self._set_text(text)

    def redo(self) -> None:
        if self._future:
            label, text = self._future.pop()
            self._history.append((label, self.text))
            self._set_text(text)

    def start_rendering(self) -> None:
        pass

    def add_font_notes(self) -> None:
        pass

    def save_copy(self, out: Path, *, unprotected: bool = False) -> list[str]:
        if self.text == self.page.text and not self._history and Path(out) != self.path:
            copy_file(self.path, out)
            return []
        data = self.text.replace("\r\n", "\n").replace("\n", self.newline).encode(self.encoding)
        temporary = Path(out).with_name(f".{Path(out).name}.saving")
        temporary.write_bytes(data)
        temporary.replace(out)
        return []

    def mark_saved(self) -> None:
        self._saved_text = self.text
        self.state_changed.emit()

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

    def mousePressEvent(self, event) -> None:
        if self.reader.editing and event.button() == Qt.MouseButton.LeftButton:
            if self.reader.open_editor_at(event.position().toPoint()):
                event.accept()
                return
        super().mousePressEvent(event)

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
    source_edited = Signal(int, int, str)  # first line, line after the last, new Markdown

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
        self._rendered_zoom = 0.0
        self._heading_tops: list[tuple[int, str]] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._refresh)
        self.selection_available = False
        self.browser.copyAvailable.connect(self._note_selection)
        self.editing = False
        self._editor: QPlainTextEdit | None = None
        self._editor_lines: tuple[int, int] = (0, 0)
        self._block_marks: list[tuple[int, int]] = []  # (character position, block number)
        self.browser.verticalScrollBar().valueChanged.connect(lambda _value: self.close_editor(True))
        self._fill_natural_sizes()

    def set_page(self, page: MarkdownPage) -> None:
        """Show an edited version, keeping the reader's place."""
        self.page = page
        self._fill_natural_sizes()
        self.render()

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
        shown = self._rendered_width > 0
        waiting = self._anchor is not None
        if shown:  # the browser still has its old size here, so this is still the reader's real place
            self._remember_anchor(QPoint(self.browser.viewport().width() // 2, 0))
        self.browser.setGeometry(self.rect())
        self._place_browser_margins()
        if self._content_width() == self._rendered_width:
            if not waiting:
                self._anchor = None
        elif shown and not self._timer.isActive():
            self.render()  # at once, so a single change such as hiding a panel shows no jump
            self._timer.start()  # the later steps of dragging the window's edge come together
        else:
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

    def _refresh(self) -> None:
        if (self._content_width(), self.zoom) != (self._rendered_width, self._rendered_zoom):
            self.render()
        else:
            self._anchor = None

    def render(self) -> None:
        anchor, self._anchor = self._anchor, None
        if anchor is None and self._rendered_width > 0:
            anchor = self.place()
        self._place_browser_margins()
        document = self.browser.document()
        ratio = self.devicePixelRatioF()
        width = self._content_width()
        self._rendered_width = width
        self._rendered_zoom = self.zoom
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
            if anchor is not None:
                self.go_to_place(anchor)
            else:
                self._finish_layout()
        finally:
            self.browser.setUpdatesEnabled(True)
        self._heading_tops = []
        QTimer.singleShot(0, self._index_headings)
        self.update()

    def _index_headings(self) -> None:
        """Remember where each heading and each block landed, for the outline and for editing."""
        if not shiboken6.isValid(self.browser):
            return  # the tab closed before this queued call ran
        document = self.browser.document()
        layout = document.documentLayout()
        tops = []
        marks: list[tuple[int, int]] = []
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    for name in fragment.charFormat().anchorNames():
                        if re.fullmatch(r"h\d+", name):
                            tops.append((round(layout.blockBoundingRect(block).top()), name))
                        elif re.fullmatch(r"src\d+", name):
                            marks.append((fragment.position(), int(name[3:])))
                iterator += 1
            block = block.next()
        self._heading_tops = tops
        self._block_marks = marks
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

    # -- editing in place ---------------------------------------------------------------------------------

    def open_editor_at(self, point: QPoint) -> bool:
        """Turn the block under `point` into its Markdown source, ready to edit."""
        if not self._block_marks:
            self._index_headings()
        position = self.browser.cursorForPosition(point).position()
        earlier = [mark for mark in self._block_marks if mark[0] <= position]
        if not earlier or not self.page.blocks:
            return False
        self.close_editor(True)
        mark_position, number = earlier[-1]
        later = [mark for mark in self._block_marks if mark[0] > mark_position]
        start, end = self.page.blocks[number]
        lines = self.page.text.split("\n")[start:end]
        while lines and not lines[-1].strip():
            lines.pop()
        cursor = QTextCursor(self.browser.document())
        cursor.setPosition(mark_position)
        top = self.browser.cursorRect(cursor).top()
        if later:
            cursor.setPosition(later[0][0])
            bottom = self.browser.cursorRect(cursor).top()
        else:
            bottom = top + 60
        editor = QPlainTextEdit(self.browser.viewport())
        font = QFont()
        font.setFamilies(["Consolas", *theme.SANS_FAMILIES])
        font.setPixelSize(round(BASE_FONT * 0.92 * self.zoom))
        editor.setFont(font)
        editor.setPlainText("\n".join(lines))
        editor.setStyleSheet(
            f"QPlainTextEdit {{ background: #fbfdfb; color: {theme.INK}; border: 2px solid {theme.ACCENT}; border-radius: 6px; padding: 4px; }}"
        )
        line_height = editor.fontMetrics().lineSpacing()
        wanted = max(bottom - top + 12, line_height * (len(lines) + 1) + 18)
        viewport = self.browser.viewport()
        height = int(min(max(wanted, line_height * 2 + 18), viewport.height() * 0.8))
        editor.setGeometry(6, int(max(0, min(top - 6, viewport.height() - height))), viewport.width() - 12, height)
        editor.installEventFilter(self)
        editor.show()
        editor.setFocus()
        self._editor = editor
        self._editor_lines = (start, start + len(lines))  # the blank lines after the block stay as they are
        return True

    def close_editor(self, apply: bool) -> None:
        editor, self._editor = self._editor, None
        if editor is None:
            return
        text = editor.toPlainText()
        original_start, original_end = self._editor_lines
        editor.removeEventFilter(self)
        editor.hide()
        editor.deleteLater()
        self.browser.setFocus()
        if apply:
            self.source_edited.emit(original_start, original_end, text)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._editor:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self.close_editor(False)
                    return True
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.close_editor(True)
                    return True
            elif event.type() == QEvent.Type.FocusOut and event.reason() != Qt.FocusReason.PopupFocusReason:
                QTimer.singleShot(0, lambda: self.close_editor(True))
        return super().eventFilter(watched, event)

    def go_to_heading(self, anchor: str) -> None:
        self.browser.scrollToAnchor(anchor)

    def _finish_layout(self) -> None:
        """Qt lays out a long document only down to the view, and until then the scroll range ends there."""
        document = self.browser.document()
        document.documentLayout().documentSizeChanged.emit(document.size())

    def place(self) -> tuple[int, int]:
        """The reader's place: the character at the top of the view and its height there. A character
        stays the same at any width or zoom, unlike a share of the scroll range."""
        cursor = self.browser.cursorForPosition(QPoint(self.browser.viewport().width() // 2, 0))
        return cursor.position(), self.browser.cursorRect(cursor).top()

    def go_to_place(self, place: tuple[int, int]) -> None:
        self._finish_layout()
        document = self.browser.document()
        bar = self.browser.verticalScrollBar()
        cursor = QTextCursor(document)
        cursor.setPosition(min(place[0], document.characterCount() - 1))
        bar.setValue(bar.value() + self.browser.cursorRect(cursor).top() - place[1])
        self._held, self._held_scroll = place, bar.value()

    def return_to(self, zoom: float, place: tuple[int, int]) -> None:
        """Take over the zoom and place from the full-screen reader."""
        self.set_zoom(zoom)
        self._timer.stop()
        self._anchor = None
        self._refresh()
        self.go_to_place(place)

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
        self.empty = widgets.body("This document has no headings.", 12)
        self.items.itemClicked.connect(lambda item: self.chosen.emit(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.items, 1)
        layout.addWidget(self.empty)
        self.set_page(page)

    def set_page(self, page: MarkdownPage) -> None:
        self.items.clear()
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
        self.empty.setVisible(not page.headings)

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
    save_in_place_requested = Signal()

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
        self.canvas.source_edited.connect(document.replace_lines)
        document.text_changed.connect(self._text_changed)
        document.state_changed.connect(self._sync_save)
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
        row.addSpacing(6)
        self.edit_toggle = QToolButton()
        self.edit_toggle.setCheckable(True)
        self.edit_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_toggle.setText("Edit")
        self.edit_toggle.setToolTip(
            "Click any paragraph, heading, list, table, code or formula to edit its Markdown in place. "
            "Click away or press Ctrl+Enter to apply, Esc to cancel."
        )
        self.edit_toggle.toggled.connect(self._edit_mode_changed)
        row.addWidget(self.edit_toggle)
        row.addStretch(1)
        self.save_in_place = QPushButton("Save")
        self.save_in_place.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_in_place.setToolTip("Save your changes to this Markdown file (Ctrl+S)")
        self.save_in_place.clicked.connect(self.save_in_place_requested)
        self.save_in_place.setEnabled(False)
        row.addWidget(self.save_in_place)
        row.addSpacing(8)
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
        self.canvas.close_editor(True)
        self.document.undo()

    def redo(self) -> None:
        self.canvas.close_editor(True)
        self.document.redo()

    def _edit_mode_changed(self, on: bool) -> None:
        self.canvas.editing = on
        if not on:
            self.canvas.close_editor(True)
        self.canvas.browser.viewport().setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.IBeamCursor)
        if on:
            self.message.emit("Click any part of the page to edit its Markdown. Click away to apply, Esc to cancel.")

    def _text_changed(self) -> None:
        self.canvas.set_page(self.document.page)
        self.outline.set_page(self.document.page)

    def _sync_save(self) -> None:
        self.save_in_place.setEnabled(self.document.dirty)

    def duplicate_selected(self) -> None:
        pass

    def copy_pages(self) -> None:
        if not self.canvas.copy_selection():
            self.message.emit("Select some text first.")

    def paste_pages(self) -> None:
        pass


class MarkdownPresenter(QWidget):
    """Markdown in true full screen: only the document, until Esc or F11."""

    finished = Signal(float, object)

    def __init__(self, document: MarkdownDocument, zoom: float, place: tuple[int, int], wheel_zooms: bool = False, show_layout: bool = False):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{document.name} - James Viewer")
        self.document = document
        self._place = place
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
        self.reader.go_to_place(self._place)
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
        self.finished.emit(self.reader.zoom, self.reader.place())
        super().closeEvent(event)


__all__ = ["MarkdownDocument", "MarkdownPresenter", "MarkdownTab", "QGuiApplication", "QSize"]
