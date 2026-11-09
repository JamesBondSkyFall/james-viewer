"""The main window: sidebar, document tabs, file handling and shortcuts."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core import paths
from ..core.errors import ViewerError, PasswordRequired, WrongPassword
from ..core.markdown_engine import MarkdownPage
from ..core.pdf_engine import read_access
from ..core.sniff import FILE_FILTER, OPEN_EXTENSIONS, sniff
from . import icons, theme, widgets
from .docview import DocumentView
from .document import Document, merge_files, prepare
from .markdown_view import MarkdownDocument, MarkdownPresenter, MarkdownTab
from .pages import HomePage, ProtectionPage, SplitMergePage
from .presenter import Presenter
from .services import Workspace
from .word_document import WordDocument

SIDEBAR_WIDTH = 232

HELP_TEXT = """
<table cellpadding="4">
<tr><td><b>F11</b></td><td>Full screen, Esc to leave</td></tr>
<tr><td><b>Ctrl + B</b></td><td>Show or hide the control panel</td></tr>
<tr><td><b>Ctrl + L</b></td><td>Show or hide the document layout</td></tr>
<tr><td><b>← → ↑ ↓, Page Up/Down, Space</b></td><td>Previous and next</td></tr>
<tr><td><b>Home, End</b></td><td>First and last</td></tr>
<tr><td><b>Ctrl + wheel, Ctrl + +, Ctrl + -</b></td><td>Zoom</td></tr>
<tr><td><b>Ctrl + 0</b></td><td>Fit to window</td></tr>
<tr><td><b>Ctrl + O, Ctrl + W, Ctrl + Tab</b></td><td>Open, close tab, next tab</td></tr>
<tr><td><b>Ctrl + S</b></td><td>Save As (never overwrites without asking)</td></tr>
<tr><td><b>Ctrl + Z, Ctrl + Y</b></td><td>Undo, redo</td></tr>
<tr><td><b>Ctrl + C, Ctrl + V, Ctrl + D, Delete</b></td><td>Copy, paste, duplicate, delete pages or slides</td></tr>
<tr><td><b>Ctrl + A</b></td><td>Select all text on the page, or all pages in Arrange</td></tr>
</table>
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.workspace = Workspace(self)
        self.views: list[DocumentView] = []
        self.presenter: Presenter | None = None
        self.nav_mode = "view"
        self._closing = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1400, 880)
        self.setMinimumSize(1020, 640)
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_sidebar())
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.home = HomePage()
        self.home.open_requested.connect(self.open_dialog)
        self.home.recent_requested.connect(lambda value: self.open_paths([value]))
        self.doc_stack = QStackedWidget()
        self.split_merge = SplitMergePage()
        self.split_merge.split_requested.connect(self.split_document)
        self.split_merge.merge_requested.connect(self.merge_documents)
        self.split_merge.message.connect(self.notify)
        self.protection = ProtectionPage()
        self.protection.unprotect_requested.connect(self.save_unprotected)
        self.protection.owner_password_requested.connect(self.enter_owner_password)
        for page in (self.home, self.doc_stack, self.split_merge, self.protection):
            self.stack.addWidget(page)
        column.addWidget(self.stack, 1)
        row.addLayout(column, 1)
        self.toast = widgets.Toast(central)

        self._install_shortcuts()
        self._load_recent()
        self._show_control_panel(self._load_settings().get("control_panel", True) is not False)
        self.split_merge.set_document(None, None)
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(60_000)
        self._idle_timer.timeout.connect(lambda: self.workspace.run(self.workspace.office.stop_if_idle, office=True))
        self._idle_timer.start()
        self._sync_nav()

    # -- construction -------------------------------------------------------------------------------

    def _build_sidebar(self) -> QWidget:
        bar = QFrame()
        self.sidebar = bar
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(18, 26, 18, 18)
        layout.setSpacing(0)
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 0, 0)
        brand.setSpacing(12)
        brand.addWidget(widgets.BrandMark(42))
        words = QVBoxLayout()
        words.setSpacing(0)
        name = QLabel(APP_NAME)
        name.setFont(theme.sans(20, theme.QFont.Weight.DemiBold, 0.6))
        tagline = QLabel("VIEW · ARRANGE · PRESENT")
        tagline.setFont(theme.sans(8, theme.QFont.Weight.Medium, 1.1))
        tagline.setStyleSheet(f"color: {theme.MUTED};")
        words.addWidget(name)
        words.addWidget(tagline)
        brand.addLayout(words)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(34)
        label_row = QHBoxLayout()
        label_row.setContentsMargins(12, 0, 12, 0)
        label_row.addWidget(widgets.eyebrow("Workspace"))
        label_row.addStretch(1)
        count = QLabel("04")
        count.setFont(theme.numeral(12))
        count.setStyleSheet(f"color: {theme.MUTED};")
        label_row.addWidget(count)
        layout.addLayout(label_row)
        layout.addSpacing(10)
        self.nav_group = QButtonGroup(self)
        self.nav_items: dict[str, widgets.NavItem] = {}
        for key, icon_name, label, number in (
            ("view", "view", "View", "01"),
            ("arrange", "arrange", "Arrange", "02"),
            ("split", "split", "Split & Merge", "03"),
            ("protection", "lock", "Protection", "04"),
        ):
            item = widgets.NavItem(icon_name, label, number)
            item.clicked.connect(lambda _checked=False, k=key: self.set_nav(k))
            self.nav_group.addButton(item)
            self.nav_items[key] = item
            layout.addWidget(item)
            layout.addSpacing(8)
        layout.addStretch(1)
        note = QLabel("Your originals stay untouched unless you say otherwise.")
        note.setWordWrap(True)
        note.setFont(theme.sans(12))
        note.setStyleSheet(f"color: {theme.MUTED};")
        note.setContentsMargins(12, 0, 8, 12)
        layout.addWidget(note)
        help_button = QToolButton()
        help_button.setText("  Shortcuts")
        help_button.setIcon(icons.icon("help", theme.MUTED, 18))
        help_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        help_button.clicked.connect(self.show_help)
        layout.addWidget(help_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(6)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {theme.LINE};")
        layout.addWidget(rule)
        layout.addSpacing(12)
        status = QHBoxLayout()
        status.setContentsMargins(12, 0, 8, 0)
        dot = QLabel("●")
        dot.setFont(theme.sans(8))
        dot.setStyleSheet(f"color: {theme.ACCENT};")
        offline = QLabel("Offline")
        offline.setFont(theme.sans(11))
        offline.setStyleSheet(f"color: {theme.MUTED};")
        version = QLabel(f"V {__version__.rsplit('.', 1)[0]}")
        version.setFont(theme.sans(10, theme.QFont.Weight.Normal, 1.0))
        version.setStyleSheet(f"color: {theme.MUTED};")
        status.addWidget(dot)
        status.addSpacing(4)
        status.addWidget(offline)
        status.addStretch(1)
        status.addWidget(version)
        layout.addLayout(status)
        return bar

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(58)
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 0, 16, 0)
        row.setSpacing(6)
        self.corner_mark = widgets.BrandMark(36)
        self.corner_mark.setToolTip(APP_NAME)
        row.addWidget(self.corner_mark)
        self.panel_toggle = widgets.tool_button("panel", "Show or hide the control panel (Ctrl+B)", checkable=True)
        self.panel_toggle.clicked.connect(self.set_control_panel)
        row.addWidget(self.panel_toggle)
        row.addSpacing(8)
        self.tabs = QTabBar()
        self.tabs.setMovable(True)
        self.tabs.setExpanding(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.hide()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabMoved.connect(self._tab_moved)
        self.tabs.installEventFilter(self)
        row.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignVCenter)
        add = widgets.tool_button("plus", "Open documents (Ctrl+O)")
        add.clicked.connect(self.open_dialog)
        row.addWidget(add)
        row.addStretch(1)
        return bar

    def _install_shortcuts(self) -> None:
        def bind(sequence, handler) -> None:
            QShortcut(QKeySequence(sequence), self, activated=handler)

        bind(QKeySequence.StandardKey.Open, self.open_dialog)
        bind(QKeySequence.StandardKey.Save, self.save)
        bind(QKeySequence.StandardKey.SaveAs, self.save_as)
        bind(Qt.Key.Key_F11, self.present)
        bind(Qt.Key.Key_F1, self.show_help)
        bind("Ctrl+B", lambda: self.set_control_panel(not self.sidebar.isVisibleTo(self)))
        bind("Ctrl+L", self.toggle_layout)
        bind("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex()) if self.tabs.count() else None)
        bind("Ctrl+Tab", self._next_tab)
        bind(QKeySequence.StandardKey.Undo, lambda: self._with_view(lambda v: v.undo()))
        bind(QKeySequence.StandardKey.Redo, lambda: self._with_view(lambda v: v.redo()))
        bind("Ctrl+Y", lambda: self._with_view(lambda v: v.redo()))
        bind("Ctrl+=", lambda: self._with_view(lambda v: v.canvas.zoom_by(1.1)))
        bind("Ctrl++", lambda: self._with_view(lambda v: v.canvas.zoom_by(1.1)))
        bind("Ctrl+-", lambda: self._with_view(lambda v: v.canvas.zoom_by(1 / 1.1)))
        bind("Ctrl+0", lambda: self._with_view(lambda v: v.canvas.set_mode("fit")))
        bind("Ctrl+D", lambda: self._with_view(lambda v: v.duplicate_selected()))
        bind(QKeySequence.StandardKey.Copy, self._copy)
        bind(QKeySequence.StandardKey.Paste, lambda: self._with_view(lambda v: v.paste_pages()))
        bind(QKeySequence.StandardKey.SelectAll, self._select_all)

    # -- current document --------------------------------------------------------------------------

    def current_view(self) -> DocumentView | None:
        index = self.tabs.currentIndex()
        return self.views[index] if 0 <= index < len(self.views) else None

    def current_document(self) -> Document | None:
        view = self.current_view()
        return view.document if view else None

    def _with_view(self, action) -> None:
        view = self.current_view()
        if view is not None:
            action(view)

    def _copy(self) -> None:
        view = self.current_view()
        if view is None:
            return
        if view.mode == "view" and view.canvas.has_selection:
            if not view.canvas.copy_selection():
                self.notify("The author of this PDF does not allow its text to be copied.")
            return
        view.copy_pages()

    def _select_all(self) -> None:
        view = self.current_view()
        if view is None:
            return
        if view.mode == "arrange":
            view.grid.selectAll()
        else:
            view.canvas.select_all()

    def _next_tab(self) -> None:
        if self.tabs.count():
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % self.tabs.count())

    def _tab_changed(self, index: int) -> None:
        if 0 <= index < len(self.views):
            self.doc_stack.setCurrentWidget(self.views[index])
        self._sync_nav()

    def _tab_moved(self, source: int, target: int) -> None:
        self.views.insert(target, self.views.pop(source))

    def eventFilter(self, watched, event) -> bool:
        if watched is self.tabs and event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabs.tabAt(event.position().toPoint())
            if index >= 0:
                self.close_tab(index)
                return True
        return super().eventFilter(watched, event)

    def set_nav(self, key: str) -> None:
        if key == "arrange" and isinstance(self.current_view(), MarkdownTab):
            self.notify("Arrange is for PDF and PowerPoint files. Markdown pages are not arranged.")
            key = "view"
        self.nav_mode = key
        self._sync_nav()

    def _sync_nav(self) -> None:
        view = self.current_view()
        if self.nav_mode == "arrange" and isinstance(view, MarkdownTab):
            self.nav_mode = "view"
        self.nav_items[self.nav_mode].setChecked(True)
        if self.nav_mode == "split":
            splittable = view if view is not None and view.document.family in ("pdf", "office") else None
            self.split_merge.set_document(splittable.document if splittable else None, (lambda: splittable._selection()) if splittable else None)
            self.stack.setCurrentWidget(self.split_merge)
        elif self.nav_mode == "protection":
            self.protection.set_document(view.document if view else None)
            self.stack.setCurrentWidget(self.protection)
        elif view is None:
            self.stack.setCurrentWidget(self.home)
        else:
            view.set_mode(self.nav_mode)
            self.stack.setCurrentWidget(self.doc_stack)
        title = f"{view.document.name} - {APP_NAME}" if view else APP_NAME
        self.setWindowTitle(title)

    def notify(self, text: str) -> None:
        self.toast.show_message(text)

    # -- opening -----------------------------------------------------------------------------------

    def open_dialog(self) -> None:
        values, _selected = QFileDialog.getOpenFileNames(self, "Open documents", self._last_folder(), FILE_FILTER)
        self.open_paths(values)

    def open_paths(self, values: list[str]) -> None:
        for value in values:
            self._open_one(Path(value), None, False)

    def _find_open(self, path: Path) -> int:
        target = os.path.normcase(os.path.abspath(path))
        for index, view in enumerate(self.views):
            if os.path.normcase(os.path.abspath(view.document.path)) == target:
                return index
        return -1

    def _open_one(self, path: Path, password: str | None, wrong: bool) -> None:
        existing = self._find_open(path)
        if existing >= 0:
            self.tabs.setCurrentIndex(existing)
            if self.nav_mode not in ("view", "arrange"):
                self.set_nav("view")
            return
        self.notify(f"Opening {path.name}…")

        def failed(error: ViewerError) -> None:
            if isinstance(error, (PasswordRequired, WrongPassword)):
                self._ask_password(path, isinstance(error, WrongPassword))
            else:
                self._forget_recent(path)
                widgets.tell(self, f"Cannot open {path.name}", str(error))

        self.workspace.run(lambda: prepare(self.workspace, path, password), self._opened, failed)

    def _ask_password(self, path: Path, wrong: bool) -> None:
        dialog = widgets.PasswordDialog(path.name, wrong=wrong, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.field.text():
            self._open_one(path, dialog.field.text(), False)

    def _opened(self, opened) -> None:
        if isinstance(opened, MarkdownPage):
            document = MarkdownDocument(opened, self)
            view = MarkdownTab(document)
            view.link_opened.connect(lambda value: self.open_paths([value]))
        else:
            document = (WordDocument if opened.kind.family == "word" else Document)(self.workspace, opened, self)
            document.add_font_notes()
            view = DocumentView(document)
        view.message.connect(self.notify)
        view.present_requested.connect(self.present)
        view.save_requested.connect(self.save_as)
        if isinstance(view, MarkdownTab):
            view.save_in_place_requested.connect(lambda v=view: self.save_to(v.document, v.document.path))
        view.details_requested.connect(lambda: self.set_nav("protection"))
        view.insert_file_requested.connect(self.insert_file)
        view.paste_requested.connect(view.paste_pages)
        document.failed.connect(lambda text, d=document: self._document_failed(d, text))
        document.state_changed.connect(lambda d=document: self._update_tab(d))
        self.views.append(view)
        self.doc_stack.addWidget(view)
        self.toast.hide()
        index = self.tabs.addTab(document.name)
        self.tabs.setTabToolTip(index, str(document.path))
        closer = widgets.tool_button("x", "Close (Ctrl+W)")
        closer.setIconSize(QSize(14, 14))
        closer.setFixedSize(20, 20)
        closer.setStyleSheet("QToolButton { padding: 1px; border-radius: 5px; }")
        closer.clicked.connect(lambda _checked=False, v=view: self.close_tab(self.views.index(v)))
        self.tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, closer)
        self.tabs.show()
        self.tabs.setCurrentIndex(index)
        if self.nav_mode not in ("view", "arrange"):
            self.nav_mode = "view"
        self._sync_nav()
        important = [note.title for note in document.notes if note.important]
        if important:
            shown = important[0] if len(important) == 1 else f"{important[0]}, and {len(important) - 1} more"
            view.banner.show_text(f"This file uses features James Viewer cannot reproduce: {shown}.")
        document.start_rendering()
        self._remember_recent(document.path)
        view.focus_page()

    def _document_failed(self, document: Document, text: str) -> None:
        if self._closing:
            return  # a message box here would keep the closing app alive until someone clicked OK
        widgets.tell(self, f"Problem with {document.name}", text)

    def _update_tab(self, document: Document) -> None:
        for index, view in enumerate(self.views):
            if view.document is document:
                self.tabs.setTabText(index, ("● " if document.dirty else "") + document.name)

    def close_tab(self, index: int) -> None:
        if not 0 <= index < len(self.views):
            return
        view = self.views[index]
        document = view.document
        if document.dirty and not widgets.ask(
            self, f"Close {document.name}?", "Your changes have not been saved and will be lost.", "Close without saving", "Keep open", danger=True,
        ):
            return
        document.close()
        self.views.pop(index)
        self.doc_stack.removeWidget(view)
        self.tabs.removeTab(index)
        self.tabs.setVisible(self.tabs.count() > 0)
        view.deleteLater()
        self._sync_nav()

    # -- full screen -------------------------------------------------------------------------------

    def present(self) -> None:
        view = self.current_view()
        if view is None or not view.document.pages:
            self.notify("Open a document first.")
            return
        if self.presenter is not None:
            return
        if isinstance(view, MarkdownTab):
            self.presenter = MarkdownPresenter(view.document, view.canvas.zoom, view.canvas.place(), view.canvas.wheel_zooms)
            self.presenter.finished.connect(self._markdown_presenter_closed)
        else:
            self.presenter = Presenter(view.document)
            self.presenter.finished.connect(self._presenter_closed)
        self.presenter.present(self.screen())

    def _presenter_closed(self, position: int) -> None:
        self.presenter = None
        view = self.current_view()
        if view is not None:
            view.go_to(position)
            view.focus_page()
        self.activateWindow()

    def _markdown_presenter_closed(self, zoom: float, place: tuple[int, int]) -> None:
        self.presenter = None
        view = self.current_view()
        if isinstance(view, MarkdownTab):
            view.canvas.return_to(zoom, place)
            view.focus_page()
        self.activateWindow()

    def _show_control_panel(self, visible: bool) -> None:
        """While the left panel is hidden, the app's icon stays at the top left."""
        self.sidebar.setVisible(visible)
        self.corner_mark.setVisible(not visible)
        self.panel_toggle.setChecked(visible)
        self.toast.left_inset = SIDEBAR_WIDTH if visible else 0

    def set_control_panel(self, visible: bool) -> None:
        self._show_control_panel(visible)
        self._save_settings(control_panel=visible)

    def toggle_layout(self) -> None:
        """Show or hide the thumbnails or the Markdown outline."""
        if isinstance(self.presenter, MarkdownPresenter):
            self.presenter.toggle_layout()
            return
        view = self.current_view()
        if view is not None:
            view.toggle_layout()

    # -- saving ------------------------------------------------------------------------------------

    def _last_folder(self) -> str:
        document = self.current_document()
        return str(document.path.parent) if document else ""

    def _save_filter(self, document: Document) -> str:
        if document.family == "pdf":
            return "PDF (*.pdf)"
        if document.family == "markdown":
            return "Markdown (*.md *.markdown)"
        if document.format == "doc":
            return "Word 97-2003 (*.doc);;Word (*.docx);;PDF (*.pdf)"
        if document.format == "docx":
            return "Word (*.docx);;PDF (*.pdf)"
        if document.format == "ppt":
            return "PowerPoint 97-2003 (*.ppt);;PowerPoint (*.pptx)"
        return "PowerPoint (*.pptx)"

    def _is_original(self, document: Document, out: Path) -> bool:
        return os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(document.path))

    def _confirm_overwrite_original(self, document: Document, out: Path) -> bool:
        if not self._is_original(document, out):
            return True
        if getattr(document, "replace_confirmed", False):
            return True  # asked once already for this document
        confirmed = widgets.ask(
            self,
            "Replace the original file?",
            f"This replaces {out.name} on disk with the current version. This is the only way James Viewer ever changes an original.",
            "Replace original",
            "Cancel",
            danger=True,
        )
        if confirmed and hasattr(document, "replace_confirmed"):
            document.replace_confirmed = True
        return confirmed

    def save(self) -> None:
        """Ctrl+S: an edited Markdown file is saved where it is. Everything else gets Save As."""
        view = self.current_view()
        if isinstance(view, MarkdownTab) and view.document.dirty:
            view.canvas.close_editor(True)
            self.save_to(view.document, view.document.path)
            return
        self.save_as()

    def save_as(self) -> None:
        document = self.current_document()
        if document is None or not document.pages:
            return
        extension = {"pdf": ".pdf", "ppt": ".ppt", "doc": ".doc", "docx": ".docx", "md": document.path.suffix}.get(document.format, ".pptx")
        suggested = document.path.with_name(f"{document.path.stem} copy{extension}")
        out, _selected = QFileDialog.getSaveFileName(self, "Save As", str(suggested), self._save_filter(document))
        if not out:
            return
        self.save_to(document, Path(out))

    def save_to(self, document: Document, out: Path, *, unprotected: bool = False) -> None:
        if not self._confirm_overwrite_original(document, out):
            return

        def finished(warnings: list[str]) -> None:
            if not unprotected:
                document.mark_saved()
            if warnings:
                widgets.tell(self, f"Saved {out.name}", "\n\n".join(warnings))
            else:
                self.notify(f"Saved {out.name}")

        self.notify(f"Saving {out.name}…")
        self.workspace.run(
            lambda: document.save_copy(out, unprotected=unprotected),
            finished,
            lambda error: widgets.tell(self, f"Could not save {out.name}", str(error)),
            office=document.format == "ppt" or document.family == "word",
        )

    def save_unprotected(self) -> None:
        document = self.current_document()
        if document is None:
            return
        extension = document.path.suffix.lower()
        suggested = document.path.with_name(f"{document.path.stem} unprotected{extension}")
        out, _selected = QFileDialog.getSaveFileName(self, "Save Unprotected Copy", str(suggested), self._save_filter(document))
        if out:
            self.save_to(document, Path(out), unprotected=True)

    def enter_owner_password(self) -> None:
        document = self.current_document()
        if document is None or document.family != "pdf":
            return
        wrong = False
        while True:
            dialog = widgets.PasswordDialog(document.name, wrong=wrong, owner=True, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.field.text():
                return
            try:
                access = read_access(document.raw_copy, dialog.field.text())
            except WrongPassword:
                wrong = True
                continue
            except ViewerError as exc:
                widgets.tell(self, "Could not check the password", str(exc))
                return
            if not access.owner_verified:
                wrong = True
                continue
            document.access = access
            document.password = dialog.field.text()
            self.protection.set_document(document)
            document.state_changed.emit()
            self.notify("Owner password accepted.")
            return

    # -- inserting, splitting, merging ------------------------------------------------------------

    def insert_file(self) -> None:
        view = self.current_view()
        if view is None:
            return
        document = view.document
        file_filter = "PDF (*.pdf)" if document.family == "pdf" else "PowerPoint (*.ppt *.pptx)"
        value, _selected = QFileDialog.getOpenFileName(self, "Choose a file to insert from", self._last_folder(), file_filter)
        if value:
            self._insert_from(view, Path(value), None)

    def _insert_from(self, view: DocumentView, path: Path, password: str | None) -> None:
        document = view.document

        def work():
            opened = prepare(self.workspace, path, password)
            if opened.kind.family != document.family:
                raise ViewerError("PDF pages and PowerPoint slides cannot be mixed in one document.")
            if document.family == "pdf" and not opened.access.can_assemble:
                raise ViewerError(f"The author of {path.name} does not allow its pages to be copied.")
            if opened.kind.format == "ppt":
                from ..core.pptx_engine import read_deck_info
                from .document import new_page

                converted = self.workspace.session.new_dir("insert") / "converted.pptx"
                self.workspace.office.to_pptx(Path(opened.source_copy), converted)
                info = read_deck_info(converted)
                return [new_page(str(converted), s.index, hidden=s.hidden, numbered=s.has_slide_number) for s in info.slides]
            return list(opened.pages)

        def failed(error: ViewerError) -> None:
            if isinstance(error, (PasswordRequired, WrongPassword)):
                dialog = widgets.PasswordDialog(path.name, wrong=isinstance(error, WrongPassword), parent=self)
                if dialog.exec() == QDialog.DialogCode.Accepted and dialog.field.text():
                    self._insert_from(view, path, dialog.field.text())
            else:
                widgets.tell(self, f"Cannot insert from {path.name}", str(error))

        def done(pages) -> None:
            at = view._selection()[-1] + 1 if document.pages else 0
            try:
                document.insert_pages(pages, at, f"Insert from {path.name}")
            except ViewerError as exc:
                self.notify(str(exc))
            else:
                self.notify(f"Inserted {len(pages)} {document.noun}{'' if len(pages) == 1 else 's'}")

        self.workspace.run(work, done, failed, office=document.family == "office")

    def split_document(self, groups: list, each_range: bool) -> None:
        document = self.current_document()
        if document is None:
            return
        if document.family == "pdf" and not document.access.can_assemble:
            widgets.tell(self, "Splitting is not allowed", document.edit_block_reason)
            return
        if document.deck is not None and document.deck.modify_protected:
            widgets.tell(self, "Splitting is not allowed", document.edit_block_reason)
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for the new files", str(document.path.parent))
        if not folder:
            return
        extension = ".pdf" if document.family == "pdf" else ".pptx"
        stem = document.path.stem
        sets = groups if each_range else [[position for group in groups for position in group]]
        jobs = []
        for group in sets:
            label = f"{group[0] + 1}" if len(group) == 1 else f"{group[0] + 1}-{group[-1] + 1}"
            jobs.append((group, paths.unique_path(Path(folder) / f"{stem} ({label}){extension}")))

        def work() -> list[str]:
            warnings: list[str] = []
            for group, out in jobs:
                warnings.extend(document.export_positions(group, out))
            return warnings

        def done(warnings: list[str]) -> None:
            names = ", ".join(out.name for _group, out in jobs[:3]) + ("…" if len(jobs) > 3 else "")
            self.notify(f"Created {len(jobs)} file{'' if len(jobs) == 1 else 's'}: {names}")
            if warnings:
                widgets.tell(self, "Split finished", "\n\n".join(sorted(set(warnings))))

        self.notify("Splitting…")
        self.workspace.run(work, done, lambda error: widgets.tell(self, "Could not split", str(error)), office=document.format == "ppt")

    def merge_documents(self, values: list) -> None:
        try:
            families = {sniff(value).family for value in values}
        except ViewerError as exc:
            widgets.tell(self, "Cannot merge", str(exc))
            return
        if families & {"markdown", "word"}:
            widgets.tell(self, "Cannot merge", "Markdown and Word files cannot be merged here. Only PDF and PowerPoint files can.")
            return
        if len(families) != 1:
            widgets.tell(self, "Cannot merge", "PDF files and PowerPoint files cannot be merged together.")
            return
        is_pdf = families == {"pdf"}
        first = Path(values[0])
        suggested = first.with_name(f"{first.stem} merged{'.pdf' if is_pdf else '.pptx'}")
        out, _selected = QFileDialog.getSaveFileName(self, "Save merged file", str(suggested), "PDF (*.pdf)" if is_pdf else "PowerPoint (*.pptx)")
        if not out:
            return
        out_path = Path(out)
        if any(os.path.normcase(os.path.abspath(value)) == os.path.normcase(os.path.abspath(out_path)) for value in values):
            widgets.tell(self, "Choose a different name", "The merged file cannot replace one of the files being merged.")
            return
        self._run_merge([(Path(value), None) for value in values], out_path)

    def _run_merge(self, inputs: list, out_path: Path) -> None:
        def failed(error: ViewerError) -> None:
            if isinstance(error, (PasswordRequired, WrongPassword)):
                self._merge_password(inputs, out_path, isinstance(error, WrongPassword))
            else:
                widgets.tell(self, "Could not merge", str(error))

        def done(warnings: list[str]) -> None:
            if warnings:
                widgets.tell(self, f"Created {out_path.name}", "\n\n".join(warnings))
            if widgets.ask(self, f"Created {out_path.name}", "Open the merged file now?", "Open", "Not now"):
                self.open_paths([str(out_path)])

        self.notify("Merging…")
        self.workspace.run(lambda: merge_files(self.workspace, inputs, out_path), done, failed, office=True)

    def _merge_password(self, inputs: list, out_path: Path, wrong: bool) -> None:
        """Ask for the first input that still needs a password, then try the merge again."""
        for position, (path, password) in enumerate(inputs):
            needs = False
            try:
                needs = sniff(path).encrypted and (password is None or wrong)
            except ViewerError:
                pass
            if needs:
                dialog = widgets.PasswordDialog(path.name, wrong=wrong and password is not None, parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.field.text():
                    return
                inputs[position] = (path, dialog.field.text())
                self._run_merge(inputs, out_path)
                return

    # -- settings and recent files ------------------------------------------------------------------

    def _load_settings(self) -> dict:
        try:
            values = json.loads((paths.data_dir() / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return values if isinstance(values, dict) else {}

    def _save_settings(self, **changes) -> None:
        try:
            (paths.data_dir() / "settings.json").write_text(json.dumps({**self._load_settings(), **changes}), encoding="utf-8")
        except OSError:
            pass

    def _recent_path(self) -> Path:
        return paths.data_dir() / "recent.json"

    def _load_recent(self) -> None:
        try:
            values = json.loads(self._recent_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            values = []
        self._recent = [value for value in values if isinstance(value, str)][:8]
        self.home.set_recent(self._recent)

    def _save_recent(self) -> None:
        try:
            self._recent_path().write_text(json.dumps(self._recent), encoding="utf-8")
        except OSError:
            pass
        self.home.set_recent(self._recent)

    def _remember_recent(self, path: Path) -> None:
        value = str(path)
        self._recent = [value] + [item for item in self._recent if item != value]
        self._recent = self._recent[:8]
        self._save_recent()

    def _forget_recent(self, path: Path) -> None:
        if str(path) in self._recent:
            self._recent.remove(str(path))
            self._save_recent()

    # -- window ------------------------------------------------------------------------------------

    def show_help(self) -> None:
        widgets.tell(self, "Keyboard shortcuts", HELP_TEXT)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(
            Path(url.toLocalFile()).suffix.lower() in OPEN_EXTENSIONS for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        values = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        accepted = [value for value in values if Path(value).suffix.lower() in OPEN_EXTENSIONS]
        event.acceptProposedAction()
        if self.nav_mode == "split":
            for value in accepted:
                self.split_merge.add_merge_file(value)
            self.notify(f"Added {len(accepted)} file(s) to merge")
            return
        self.open_paths(accepted)
        skipped = len(values) - len(accepted)
        if skipped:
            self.notify(f"Skipped {skipped} file(s) that are not PDF, PowerPoint, Word or Markdown files")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._tint_title_bar()

    def _tint_title_bar(self) -> None:
        """Match the native title bar to the window, keeping Windows' own snapping and shadow."""
        try:
            hwnd = int(self.winId())
            color = QColor(theme.SIDEBAR)
            value = ctypes.c_int(color.red() | (color.green() << 8) | (color.blue() << 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(value), ctypes.sizeof(value))
            ink = QColor(theme.INK)
            text = ctypes.c_int(ink.red() | (ink.green() << 8) | (ink.blue() << 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        except (OSError, AttributeError):
            pass

    def closeEvent(self, event) -> None:
        unsaved = [view.document.name for view in self.views if view.document.dirty]
        if unsaved:
            names = ", ".join(unsaved[:3]) + ("…" if len(unsaved) > 3 else "")
            if not widgets.ask(self, "Quit without saving?", f"These have unsaved changes: {names}", "Quit without saving", "Keep working", danger=True):
                event.ignore()
                return
        self._closing = True
        if self.presenter is not None:
            self.presenter.close()
        for view in self.views:
            view.document.close()
        self.workspace.shutdown()
        event.accept()


__all__ = ["MainWindow", "QAction", "QApplication", "QFontMetrics", "QRectF"]
