"""One document's working area: reading view with thumbnails, and the Arrange grid."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core.errors import ViewerError
from ..core.pptx_engine import LINE_BREAK
from . import icons, theme, widgets
from .canvas import PageCanvas
from .document import Document
from .pagelist import PagesView


class TextPanel(QFrame):
    """Edit the text already on a slide. Each box maps to one text shape, formatting preserved."""

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        self.setObjectName("TextPanel")
        self.setFixedWidth(340)
        self.position = -1
        self._blocks: list = []
        self._editors: list[QPlainTextEdit] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 14, 16)
        layout.setSpacing(6)
        layout.addWidget(widgets.eyebrow("Edit text"))
        self.heading = widgets.title("Slide", 17)
        layout.addWidget(self.heading)
        self.word = document.family == "word"
        hint = (
            "Each box is one paragraph on this page, and its formatting is kept. Shift+Enter makes a line break. "
            "Paragraphs holding page numbers or a table of contents cannot be edited here."
            if self.word else
            "Press Enter for a new paragraph and Shift+Enter for a line break. Text in charts and SmartArt cannot be edited here."
        )
        self.hint = widgets.body(hint, 12)
        layout.addWidget(self.hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.holder.setStyleSheet("background: transparent;")
        self.stack = QVBoxLayout(self.holder)
        self.stack.setContentsMargins(0, 8, 8, 8)
        self.stack.setSpacing(6)
        self.scroll.setWidget(self.holder)
        layout.addWidget(self.scroll, 1)
        self.font_box = QCheckBox("Times New Roman for my changes")
        self.font_box.setToolTip("English letters in the paragraphs you change use Times New Roman. Chinese keeps its font.")
        self.font_all = QPushButton("Times New Roman for all English text")
        self.font_all.setCheckable(True)
        self.font_all.setToolTip("English letters everywhere in the document use Times New Roman. Chinese keeps its font.")
        self.font_all.clicked.connect(self._font_everywhere)
        if self.word:
            layout.addWidget(self.font_box)
            layout.addWidget(self.font_all)
        row = QHBoxLayout()
        row.addStretch(1)
        self.revert = QPushButton("Revert")
        self.revert.clicked.connect(self.load)
        self.apply = widgets.primary_button("Apply")
        self.apply.clicked.connect(self.commit)
        row.addWidget(self.revert)
        row.addWidget(self.apply)
        layout.addLayout(row)

    def show_position(self, position: int) -> None:
        if position != self.position:
            self.position = position
            self.load()

    def load(self) -> None:
        while self.stack.count():
            item = self.stack.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._editors = []
        self._blocks = []
        if not 0 <= self.position < len(self.document.pages):
            return
        self.heading.setText(f"{self.document.noun.capitalize()} {self.position + 1}")
        if self.word:
            self.font_all.setChecked(self.document.font_everywhere)
        try:
            self._blocks = self.document.text_blocks(self.position)
        except ViewerError as exc:
            self.stack.addWidget(widgets.body(str(exc)))
            return
        if not self._blocks:
            self.stack.addWidget(widgets.body(f"This {self.document.noun} has no editable text."))
        for block in self._blocks:
            caption = QLabel(block.name)
            caption.setFont(theme.sans(11, theme.QFont.Weight.Medium))
            caption.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
            self.stack.addWidget(caption)
            editor = QPlainTextEdit()
            editor.setPlainText(block.text)
            lines = max(2, min(8, block.text.count("\n") + block.text.count(LINE_BREAK) + 2 + len(block.text) // 34))
            editor.setFixedHeight(lines * 20 + 18)
            self.stack.addWidget(editor)
            self._editors.append(editor)
        self.stack.addStretch(1)

    @staticmethod
    def _raw_text(editor: QPlainTextEdit) -> str:
        return editor.document().toRawText().replace(" ", "\n")

    def commit(self) -> None:
        changes = [
            (block.index, self._raw_text(editor))
            for block, editor in zip(self._blocks, self._editors)
            if self._raw_text(editor) != block.text
        ]
        if self.word:
            self.document.edit_text(self.position, changes, latin_font=self.font_box.isChecked())
            self.load()
        elif changes:
            self.document.edit_text(self.position, changes)
            self.load()

    def _font_everywhere(self, on: bool) -> None:
        try:
            self.document.set_font_everywhere(on)
        except ViewerError:
            self.font_all.setChecked(not on)


class DocumentView(QWidget):
    message = Signal(str)
    present_requested = Signal()
    save_requested = Signal()
    details_requested = Signal()
    insert_file_requested = Signal()
    paste_requested = Signal()

    def __init__(self, document: Document, parent=None):
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
        self.modes = QStackedWidget()
        self.modes.addWidget(self._build_reader())
        self.modes.addWidget(self._build_arrange())
        outer.addWidget(self.modes, 1)

        document.pages_changed.connect(self._pages_changed)
        document.state_changed.connect(self._sync)
        document.renders_changed.connect(self._sync)
        document.status.connect(self._status)
        self._sync()
        self.go_to(document.current)

    # -- layout ---------------------------------------------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Toolbar")
        bar.setFixedHeight(58)
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(4)
        self.layout_toggle = widgets.tool_button("arrange", "Show or hide thumbnails (Ctrl+L)", checkable=True)
        self.layout_toggle.setChecked(True)
        self.layout_toggle.clicked.connect(self._layout_clicked)
        row.addWidget(self.layout_toggle)
        row.addSpacing(12)
        self.page_number = QLabel("1")
        self.page_number.setFont(theme.numeral(20))
        self.page_number.setStyleSheet(f"color: {theme.INK};")
        self.page_total = QLabel("/ 1")
        self.page_total.setFont(theme.sans(13))
        self.page_total.setStyleSheet(f"color: {theme.MUTED};")
        row.addWidget(self.page_number)
        row.addSpacing(4)
        row.addWidget(self.page_total)
        row.addSpacing(24)
        self.zoom_out = widgets.tool_button("minus", "Zoom out (Ctrl+-)")
        self.zoom_label = QToolButton()
        self.zoom_label.setText("Fit")
        self.zoom_label.setFixedWidth(62)
        self.zoom_label.setToolTip("Zoom options")
        self.zoom_label.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.zoom_in = widgets.tool_button("plus", "Zoom in (Ctrl++)")
        row.addWidget(self.zoom_out)
        row.addWidget(self.zoom_label)
        row.addWidget(self.zoom_in)
        row.addSpacing(10)
        divider = QFrame()
        divider.setFixedSize(1, 22)
        divider.setStyleSheet(f"background: {theme.LINE_STRONG};")
        row.addWidget(divider)
        row.addSpacing(10)
        self.undo_button = widgets.tool_button("undo", "Undo")
        self.redo_button = widgets.tool_button("redo", "Redo")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        row.addWidget(self.undo_button)
        row.addWidget(self.redo_button)
        self.text_toggle = widgets.tool_button("text", "Edit text", checkable=True)
        self.text_toggle.clicked.connect(self._toggle_text_panel)
        row.addWidget(self.text_toggle)
        self.mark_buttons = {}
        for name, tip in (
            ("highlight", "Highlight the selected text"),
            ("underline", "Underline the selected text"),
        ):
            button = widgets.tool_button(name, tip)
            button.clicked.connect(lambda _checked=False, kind=name: self._mark(kind))
            row.addWidget(button)
            self.mark_buttons[name] = button
        self.tool_buttons = {}
        for name, icon_name, tip in (
            ("note", "note", "Add a note: click here, then click on the page"),
            ("text", "textbox", "Add a text box: click here, then click on the page"),
            ("ink", "ink", "Draw: drag on the page with the mouse or a pen"),
        ):
            button = widgets.tool_button(icon_name, tip, checkable=True)
            button.toggled.connect(lambda checked, tool=name: self._tool_toggled(tool, checked))
            row.addWidget(button)
            self.tool_buttons[name] = button
        row.addSpacing(14)
        self.status_label = QLabel("")
        self.status_label.setFont(theme.sans(12))
        self.status_label.setStyleSheet(f"color: {theme.MUTED};")
        row.addWidget(self.status_label)
        row.addStretch(1)
        self.save_button = QPushButton("Save As")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.clicked.connect(self.save_requested)
        row.addWidget(self.save_button)
        row.addSpacing(8)
        self.present_button = widgets.primary_button("Present  F11")
        self.present_button.clicked.connect(self.present_requested)
        row.addWidget(self.present_button)
        return bar

    def _build_reader(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        rail = QFrame()
        self.rail_frame = rail
        rail.setObjectName("Rail")
        rail.setFixedWidth(214)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(4, 10, 4, 4)
        self.rail = PagesView(self.document, grid=False, thumb_width=150)
        self.rail.activated_page.connect(self.go_to)
        self.rail.delete_requested.connect(self.delete_selected)
        rail_layout.addWidget(self.rail)
        row.addWidget(rail)

        self.canvas = PageCanvas(self.document)
        self.canvas.page_requested.connect(self.go_to)
        self.canvas.zoom_changed.connect(self.zoom_label.setText)
        self.canvas.fullscreen_requested.connect(self.present_requested)
        self.canvas.text_copied.connect(lambda n: self.message.emit(f"Copied {n} characters"))
        self.canvas.place_requested.connect(self._place)
        self.canvas.ink_drawn.connect(self._ink)
        row.addWidget(self.canvas, 1)
        self.text_panel = TextPanel(self.document)
        self.text_panel.hide()
        row.addWidget(self.text_panel)

        self.zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.2))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.2))
        menu = QMenu(self.zoom_label)
        menu.addAction("Fit to window").triggered.connect(lambda: self.canvas.set_mode("fit"))
        menu.addAction("Fit to width").triggered.connect(lambda: self.canvas.set_mode("width"))
        menu.addSeparator()
        for percent in (50, 75, 100, 150, 200):
            menu.addAction(f"{percent}%").triggered.connect(lambda _checked=False, p=percent: self.canvas.set_zoom(p / 100))
        self.zoom_label.setMenu(menu)
        return page

    def _build_arrange(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 22, 22, 14)
        layout.setSpacing(8)
        head = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(3)
        self.arrange_eyebrow = widgets.eyebrow("Arrange")
        self.arrange_title = widgets.title("Nothing selected")
        self.arrange_hint = widgets.body("Drag to reorder. Copy and paste also works between open tabs.", wrap=False)
        words.addWidget(self.arrange_eyebrow)
        words.addWidget(self.arrange_title)
        words.addWidget(self.arrange_hint)
        head.addLayout(words)
        head.addStretch(1)
        self.arrange_count = widgets.numeral("00", 52)
        head.addWidget(self.arrange_count)
        head.addSpacing(18)
        self.copy_button = QPushButton("Copy")
        self.paste_button = QPushButton("Paste")
        self.duplicate_button = QPushButton("Duplicate")
        self.delete_button = QPushButton("Delete")
        self.add_button = widgets.primary_button("Add")
        self.add_button.setIcon(icons.icon("plus", "#ffffff", 18))
        self.copy_button.clicked.connect(self.copy_pages)
        self.paste_button.clicked.connect(self.paste_requested)
        self.duplicate_button.clicked.connect(self.duplicate_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.add_menu = QMenu(self.add_button)
        self.add_button.setMenu(self.add_menu)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        for button in (self.copy_button, self.paste_button, self.duplicate_button, self.delete_button, self.add_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            head.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)
        panel = widgets.Card()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 16, 20, 24)
        self.grid = PagesView(self.document, grid=True, thumb_width=236)
        self.grid.delete_requested.connect(self.delete_selected)
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self._update_arrange_title())
        self.grid.doubleClicked.connect(self._open_from_grid)
        panel_layout.addWidget(self.grid)
        layout.addWidget(panel, 1)
        return page

    # -- state ----------------------------------------------------------------------------------------

    def _layout_clicked(self, checked: bool) -> None:
        self.rail_frame.setVisible(checked)
        self.canvas.refresh()

    def toggle_layout(self) -> None:
        self.layout_toggle.setChecked(not self.layout_toggle.isChecked())
        self._layout_clicked(self.layout_toggle.isChecked())

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.modes.setCurrentIndex(0 if mode == "view" else 1)
        for widget in (self.zoom_out, self.zoom_in, self.zoom_label, self.layout_toggle):
            widget.setVisible(mode == "view")
        self.text_toggle.setVisible(mode == "view" and self.document.family in ("office", "word"))
        for button in list(self.mark_buttons.values()) + list(self.tool_buttons.values()):
            button.setVisible(mode == "view" and self.document.family == "pdf")
        if mode != "view":
            for button in self.tool_buttons.values():
                button.setChecked(False)
        if mode == "arrange":
            self.grid.select_position(self.document.current)
            self.grid.setFocus()
        else:
            self.canvas.setFocus()
        self._sync()

    def _pages_changed(self) -> None:
        self.go_to(self.document.current)
        self._sync()

    def _sync(self) -> None:
        document = self.document
        total = len(document.pages)
        self.page_number.setText(str(document.current + 1 if total else 0))
        self.page_total.setText(f"/ {total}" if total else "")
        self.undo_button.setEnabled(bool(document.undo_label))
        self.redo_button.setEnabled(bool(document.redo_label))
        self.undo_button.setToolTip(f"Undo: {document.undo_label} (Ctrl+Z)" if document.undo_label else "Nothing to undo")
        self.redo_button.setToolTip(f"Redo: {document.redo_label} (Ctrl+Y)" if document.redo_label else "Nothing to redo")
        self.present_button.setEnabled(total > 0)
        self.save_button.setEnabled(total > 0)
        editable = document.can_edit_pages
        for button in (self.duplicate_button, self.delete_button, self.add_button, self.paste_button):
            button.setEnabled(editable and total > 0)
        self.text_toggle.setEnabled(document.can_edit_text)
        for button in list(self.mark_buttons.values()) + list(self.tool_buttons.values()):
            button.setEnabled(document.can_annotate and total > 0)
        self.arrange_eyebrow.setText(f"ARRANGE {document.noun.upper()}S")
        if document.preparing:
            done = sum(1 for i in range(total) if document.ref_at(i) is not None)
            self.status_label.setText(f"Preparing {document.noun}s… {done} of {total}" if total else "Preparing slides…")
        elif not document.status_text():
            self.status_label.setText("Unsaved changes" if document.dirty else "")
        self._update_arrange_title()
        self.rail.viewport().update()
        self.grid.viewport().update()

    def _status(self, text: str) -> None:
        self.document._status_text = text
        if text:
            self.status_label.setText(text)
        else:
            self._sync()

    def _update_arrange_title(self) -> None:
        count = len(self.grid.selectionModel().selectedIndexes())
        noun = self.document.noun
        self.arrange_title.setText(f"{count} {noun}{'' if count == 1 else 's'} selected" if count else f"{len(self.document.pages)} {noun}s")
        self.arrange_count.setText(f"{count:02d}" if count else f"{len(self.document.pages):02d}")
        hint = self.document.edit_block_reason or "Drag to reorder. Copy and paste also works between open tabs."
        self.arrange_hint.setText(hint)

    def go_to(self, position: int) -> None:
        total = len(self.document.pages)
        if not total:
            return
        position = max(0, min(position, total - 1))
        self.document.current = position
        self.canvas.verticalScrollBar().setValue(0)
        self.canvas.refresh()
        self.page_number.setText(str(position + 1))
        selected = {i.row() for i in self.rail.selectionModel().selectedIndexes()}
        if position not in selected or len(selected) <= 1:
            self.rail.select_position(position)
        self.rail.viewport().update()
        if self.text_panel.isVisible():
            self.text_panel.show_position(position)

    def focus_page(self) -> None:
        self.canvas.setFocus()

    # -- editing --------------------------------------------------------------------------------------

    def _selection(self) -> list[int]:
        view = self.grid if self.mode == "arrange" else self.rail
        return view.selected_positions()

    def _guard(self, text: bool = False) -> bool:
        """Editing needs a PPTX or DOCX, so a .ppt or .doc is converted first, after asking."""
        document = self.document
        if not (document.can_edit_text if text else document.can_edit_pages):
            self.message.emit(document.edit_block_reason)
            return False
        if document.preparing:
            self.message.emit("This document is still being prepared.")
            return False
        if document.format not in ("ppt", "doc"):
            return True
        word = document.format == "doc"
        modern, kind = ("DOCX", "Word") if word else ("PPTX", "PowerPoint")
        if widgets.ask(
            self,
            f"Convert to {modern} for editing?",
            f"{kind} 97-2003 files are edited as {modern}. James Viewer will work on a converted copy and "
            f"your original .{document.format} will not be changed. Some formatting may shift.",
            "Convert",
        ):
            self.status_label.setText(f"Converting to {modern}…")
            document.convert_for_editing(lambda: self.message.emit("Converted. You can edit this document now."))
        return False

    def _try(self, action, *args) -> None:
        try:
            action(*args)
        except ViewerError as exc:
            self.message.emit(str(exc))

    def undo(self) -> None:
        self.document.undo()

    def redo(self) -> None:
        self.document.redo()

    def delete_selected(self) -> None:
        if self._guard():
            positions = self._selection()
            self._try(self.document.delete, positions)

    def duplicate_selected(self) -> None:
        if self._guard():
            self._try(self.document.duplicate, self._selection())

    def add_slide(self, layout: str | None = None) -> None:
        if self._guard():
            at = self._selection()[-1] + 1 if self.document.pages else 0
            self._try(self.document.add_slide, at, layout)

    def add_blank_page(self) -> None:
        if self._guard():
            at = self._selection()[-1] + 1 if self.document.pages else 0
            self._try(self.document.add_blank_pdf_page, at)

    def _fill_add_menu(self) -> None:
        self.add_menu.clear()
        document = self.document
        if document.family == "pdf":
            self.add_menu.addAction("Blank page").triggered.connect(self.add_blank_page)
            self.add_menu.addAction("Pages from a PDF file…").triggered.connect(self.insert_file_requested)
            return
        self.add_menu.addAction("New slide (same layout)").triggered.connect(lambda: self.add_slide())
        if document.deck and document.deck.layouts:
            layouts = self.add_menu.addMenu("New slide with layout")
            for part, name in document.deck.layouts[:40]:
                layouts.addAction(name).triggered.connect(lambda _checked=False, p=part: self.add_slide(p))
        self.add_menu.addAction("Slides from a PowerPoint file…").triggered.connect(self.insert_file_requested)

    def copy_pages(self) -> None:
        document = self.document
        if document.family == "pdf" and not document.access.can_assemble:
            self.message.emit(document.edit_block_reason)
            return
        if document.format == "ppt":
            self.message.emit("Convert this .ppt to PPTX first: choose Duplicate, Delete or Edit text.")
            return
        positions = self._selection()
        document.workspace.page_clipboard = {
            "family": document.family,
            "pages": [document.pages[i] for i in positions],
            "noun": document.noun,
        }
        count = len(positions)
        self.message.emit(f"Copied {count} {document.noun}{'' if count == 1 else 's'}")

    def paste_pages(self) -> None:
        clip = self.document.workspace.page_clipboard
        if not clip:
            self.message.emit(f"Copy some {self.document.noun}s first.")
            return
        if clip["family"] != self.document.family:
            self.message.emit("PDF pages and PowerPoint slides cannot be mixed in one document.")
            return
        if self._guard():
            at = self._selection()[-1] + 1
            count = len(clip["pages"])
            self._try(self.document.insert_pages, clip["pages"], at, f"Paste {count} {self.document.noun}{'' if count == 1 else 's'}")

    # -- PDF annotations --------------------------------------------------------------------------------------

    def _annotation_blocked(self) -> bool:
        document = self.document
        if not document.can_annotate:
            self.message.emit("The author of this PDF does not allow comments or highlights to be added.")
            return True
        page = document.pages[document.current]
        ref = document.ref_at(document.current)
        from ..core.pdf_annotations import page_rotation

        if page_rotation(page.source, ref.password if ref else document.password, page.index):
            self.message.emit("Annotations on rotated pages are not supported yet.")
            return True
        return False

    def _mark(self, kind: str) -> None:
        if not self.canvas.has_selection:
            self.message.emit(f"Select some text on the page first, then choose {kind.capitalize()}.")
            return
        if self._annotation_blocked():
            return
        rects = self.canvas.selection_rects()
        self.canvas.clear_selection()
        self._try(self.document.annotate, self.document.current, {"type": kind, "rects": rects}, kind.capitalize())

    def _tool_toggled(self, tool: str, checked: bool) -> None:
        if checked:
            for name, button in self.tool_buttons.items():
                if name != tool and button.isChecked():
                    button.setChecked(False)
            self.canvas.tool = tool
        elif self.canvas.tool == tool:
            self.canvas.tool = None

    def _place(self, tool: str, x: float, y: float) -> None:
        if self._annotation_blocked():
            return
        heading = "Add a note" if tool == "note" else "Add a text box"
        text = widgets.ask_text(self, heading, "Text:")
        if not text:
            return
        if tool == "note":
            self._try(self.document.annotate, self.document.current, {"type": "note", "x": x, "y": y, "text": text}, "Add note")
            return
        from .annotation_text import measure

        width, height = measure(text, 12)
        rect = [x, y - height, x + width, y]
        self._try(self.document.annotate, self.document.current, {"type": "text", "rect": rect, "text": text, "size": 12}, "Add text box")

    def _ink(self, points: list) -> None:
        if not self._annotation_blocked():
            self._try(self.document.annotate, self.document.current, {"type": "ink", "paths": [points], "width": 2}, "Draw")

    def _toggle_text_panel(self, checked: bool) -> None:
        if checked and not self._guard(text=True):
            self.text_toggle.setChecked(False)
            return
        self.text_panel.setVisible(checked)
        if checked:
            self.text_panel.position = -1
            self.text_panel.show_position(self.document.current)
        self.canvas.refresh()

    def _open_from_grid(self, index) -> None:
        self.go_to(index.row())
        self.mode_requested("view")

    def mode_requested(self, mode: str) -> None:
        window = self.window()
        if hasattr(window, "set_nav"):
            window.set_nav(mode)


__all__ = ["DocumentView", "QFontMetrics", "QSize", "QSizePolicy", "QTimer"]
