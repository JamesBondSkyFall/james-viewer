"""The Home, Split & Merge and Protection pages."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core.errors import ViewerError
from ..core.ranges import format_ranges, parse_ranges
from ..core.sniff import MERGE_FILTER
from . import icons, theme, widgets


class SheetsArt(QWidget):
    """Layered sheets that lean toward the pointer, the app's one piece of pure decoration."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 200)
        self._tilt = QPointF(0, 0)

    def set_pointer(self, position: QPointF) -> None:
        x = (position.x() - self.width() / 2) / max(self.width(), 1)
        y = (position.y() - self.height() / 2) / max(self.height(), 1)
        self._tilt = QPointF(max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y)))
        self.update()

    def mouseMoveEvent(self, event) -> None:
        self.set_pointer(event.position())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        # The stack spreads about 1.4 sheet widths and 1.05 sheet heights, so size it to stay inside.
        size = min(self.width() * 0.6, self.height() * 0.84)
        sheet_w, sheet_h = size, size * 0.62
        center += QPointF(-size * 0.1, size * 0.13)
        layers = [(-3, 0.22), (-2, 0.42), (-1, 0.72), (0, 1.0)]
        for depth, alpha in layers:
            shift = depth * size * 0.13
            parallax = (depth + 3.5) * 5
            transform = QTransform()
            transform.translate(
                center.x() - shift * 0.8 * -1 + self._tilt.x() * parallax,
                center.y() + shift * 0.62 + self._tilt.y() * parallax,
            )
            transform.rotate(-6)
            transform.shear(-0.12, 0)
            painter.save()
            painter.setTransform(transform)
            rect = QRectF(-sheet_w / 2, -sheet_h / 2, sheet_w, sheet_h)
            if depth == 0:
                theme.soft_shadow(painter, rect, 10, 18, 0.16, 12)
                painter.setPen(QPen(QColor("#ffffff"), 1.2))
                painter.setBrush(QColor("#ffffff"))
            else:
                gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
                first, last = QColor(theme.ACCENT_LIGHT), QColor(theme.ACCENT_DARK)
                first.setAlphaF(alpha * 0.9)
                last.setAlphaF(alpha)
                gradient.setColorAt(0, first)
                gradient.setColorAt(1, last)
                painter.setBrush(gradient)
                edge = QColor("#ffffff")
                edge.setAlphaF(0.65)
                painter.setPen(QPen(edge, 1.0))
            painter.drawRoundedRect(rect, 10, 10)
            if depth == 0:
                band = QRectF(rect.left() + 20, rect.top() + 20, rect.width() * 0.46, 11)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.accent_gradient(band))
                painter.drawRoundedRect(band, 5.5, 5.5)
                painter.setBrush(QColor("#e4ece7"))
                for line in range(3):
                    width = rect.width() * (0.8 - line * 0.14)
                    painter.drawRoundedRect(QRectF(rect.left() + 20, rect.top() + 48 + line * 20, width, 7), 3.5, 3.5)
            painter.restore()


class HomePage(QWidget):
    open_requested = Signal()
    recent_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 18, 22, 12)
        layout.setSpacing(4)

        hero = HeroCard()
        hero.open_requested.connect(self.open_requested)
        layout.addWidget(hero, 5)

        row = QHBoxLayout()
        row.setSpacing(4)
        recent_card = widgets.Card()
        recent_layout = recent_card.inner_layout()
        head = QHBoxLayout()
        heading = widgets.title("Recent", 16)
        head.addWidget(heading)
        head.addSpacing(10)
        head.addWidget(widgets.eyebrow("Documents"))
        head.addStretch(1)
        recent_layout.addLayout(head)
        self.recent = QListWidget()
        self.recent.setFrameShape(QFrame.Shape.NoFrame)
        self.recent.setStyleSheet(
            f"QListWidget {{ background: transparent; }} QListWidget::item {{ padding: 8px 6px; border-radius: 7px; color: {theme.INK}; }}"
            f"QListWidget::item:hover {{ background: {theme.ACCENT_FAINT}; }} QListWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.INK}; }}"
        )
        self.recent.itemActivated.connect(lambda item: self.recent_requested.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.recent.itemClicked.connect(lambda item: self.recent_requested.emit(item.data(Qt.ItemDataRole.UserRole)))
        recent_layout.addWidget(self.recent, 100)
        self.recent_empty = widgets.body("Files you open will be listed here.")
        recent_layout.addWidget(self.recent_empty)
        recent_layout.addStretch(1)
        row.addWidget(recent_card, 3)

        keys_card = widgets.Card(fill="#f7fbf8")
        keys_layout = keys_card.inner_layout()
        keys_layout.addWidget(widgets.title("Shortcuts", 16))
        for number, (key, meaning) in enumerate(
            (("F11", "True full screen"), ("← →", "Previous and next"), ("Ctrl + wheel", "Zoom"), ("F1", "All shortcuts")), 1
        ):
            line = QHBoxLayout()
            digit = QLabel(f"{number:02d}")
            digit.setFont(theme.numeral(15))
            digit.setStyleSheet(f"color: {theme.ACCENT_LIGHT}; background: transparent;")
            digit.setFixedWidth(30)
            keys = QLabel(key)
            keys.setFont(theme.sans(13, theme.QFont.Weight.Medium))
            keys.setStyleSheet(f"color: {theme.INK}; background: transparent;")
            keys.setFixedWidth(110)
            meaning_label = widgets.body(meaning, 13, wrap=False)
            meaning_label.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
            line.addWidget(digit)
            line.addWidget(keys)
            line.addWidget(meaning_label, 1)
            keys_layout.addLayout(line)
        keys_layout.addStretch(1)
        row.addWidget(keys_card, 2)
        layout.addLayout(row, 4)

    def set_recent(self, paths: list[str]) -> None:
        self.recent.clear()
        metrics = QFontMetrics(self.recent.font())
        for value in paths:
            path = Path(value)
            shown = f"{path.name}     {metrics.elidedText(str(path.parent), Qt.TextElideMode.ElideMiddle, 300)}"
            item = QListWidgetItem(icons.icon("doc", theme.MUTED), shown)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setToolTip(value)
            self.recent.addItem(item)
        self.recent_empty.setVisible(not paths)
        self.recent.setVisible(bool(paths))


class HeroCard(QFrame):
    open_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(52, 34, 34, 42)
        words = QVBoxLayout()
        words.setSpacing(10)
        words.addStretch(1)
        words.addWidget(widgets.eyebrow("PDF · PPT · PPTX · MD"))
        words.addWidget(widgets.title("Open a document", 36))
        words.addWidget(widgets.body("Read, arrange and present, entirely offline.\nDrop files anywhere in this window, or choose them here.", 13))
        words.addSpacing(10)
        row = QHBoxLayout()
        self.open_button = widgets.primary_button("  Choose files")
        self.open_button.setIcon(icons.icon("open", "#ffffff", 18))
        self.open_button.clicked.connect(self.open_requested)
        row.addWidget(self.open_button)
        row.addStretch(1)
        words.addLayout(row)
        words.addStretch(1)
        layout.addLayout(words, 3)
        self.art = SheetsArt()
        layout.addWidget(self.art, 4)

    def mouseMoveEvent(self, event) -> None:
        self.art.set_pointer(event.position() - QPointF(self.art.pos()))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(9, 5, -9, -13)
        theme.soft_shadow(painter, rect, 14, 12, 0.08, 5)
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        gradient.setColorAt(0, QColor("#fafdfb"))
        gradient.setColorAt(0.55, QColor("#ecf5ef"))
        gradient.setColorAt(1, QColor("#cfe5d7"))
        painter.setBrush(gradient)
        painter.setPen(QColor(theme.LINE))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)


class FileList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"QListWidget {{ background: {theme.SIDEBAR}; border: 1px solid {theme.LINE}; border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 7px 8px; border-radius: 6px; color: {theme.INK}; }}"
            f"QListWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.INK}; }}"
        )

    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]


class SplitMergePage(QWidget):
    split_requested = Signal(list, bool)  # groups of positions, one file per group
    merge_requested = Signal(list)
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.document = None
        self.selection_provider = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 24, 14)
        outer.setSpacing(6)
        outer.addWidget(widgets.eyebrow("Split & merge"))
        head = QHBoxLayout()
        head.addWidget(widgets.title("Make new files from your documents"))
        head.addStretch(1)
        outer.addLayout(head)
        outer.addWidget(widgets.body("Your originals are never changed. Each result is saved as a new file.", wrap=False))
        outer.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self._build_split(), 1)
        row.addWidget(self._build_merge(), 1)
        outer.addLayout(row, 1)

    def _build_split(self) -> QWidget:
        card = widgets.Card()
        layout = card.inner_layout()
        top = QHBoxLayout()
        top.addWidget(widgets.numeral("01", 26))
        top.addSpacing(8)
        top.addWidget(widgets.title("Split the open document", 17))
        top.addStretch(1)
        layout.addLayout(top)
        self.split_source = widgets.body("Open a document first.")
        layout.addWidget(self.split_source)
        layout.addSpacing(6)
        caption = QLabel("Pages or slides to keep")
        caption.setFont(theme.sans(12, theme.QFont.Weight.Medium))
        caption.setStyleSheet("background: transparent;")
        layout.addWidget(caption)
        fields = QHBoxLayout()
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("For example 1-3, 5, 8-12")
        use_selection = QPushButton("Use selection")
        use_selection.clicked.connect(self._use_selection)
        fields.addWidget(self.ranges, 1)
        fields.addWidget(use_selection)
        layout.addLayout(fields)
        layout.addSpacing(4)
        self.each_range = QRadioButton("One new file for each range")
        self.single_file = QRadioButton("One new file with all of them")
        self.each_range.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.each_range)
        group.addButton(self.single_file)
        for button in (self.each_range, self.single_file):
            button.setStyleSheet("background: transparent;")
            layout.addWidget(button)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.split_button = widgets.primary_button("Split…")
        self.split_button.clicked.connect(self._split_clicked)
        row.addWidget(self.split_button)
        layout.addLayout(row)
        return card

    def _build_merge(self) -> QWidget:
        card = widgets.Card()
        layout = card.inner_layout()
        top = QHBoxLayout()
        top.addWidget(widgets.numeral("02", 26))
        top.addSpacing(8)
        top.addWidget(widgets.title("Merge files", 17))
        top.addStretch(1)
        layout.addLayout(top)
        layout.addWidget(widgets.body("Add PDFs to make one PDF, or PPT and PPTX files to make one PPTX. Drag to set the order."))
        self.merge_list = FileList()
        layout.addWidget(self.merge_list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add files…")
        add.clicked.connect(self._add_files)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_files)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        self.merge_button = widgets.primary_button("Merge…")
        self.merge_button.clicked.connect(self._merge_clicked)
        row.addWidget(self.merge_button)
        layout.addLayout(row)
        return card

    def set_document(self, document, selection_provider) -> None:
        self.document = document
        self.selection_provider = selection_provider
        if document is None:
            self.split_source.setText("Open a document first.")
            self.split_button.setEnabled(False)
            return
        self.split_button.setEnabled(True)
        count = len(document.pages)
        self.split_source.setText(f"{document.name}: {count} {document.noun}{'' if count == 1 else 's'}")

    def _use_selection(self) -> None:
        if self.selection_provider is not None:
            self.ranges.setText(format_ranges(self.selection_provider()))

    def _split_clicked(self) -> None:
        if self.document is None:
            return
        try:
            groups = parse_ranges(self.ranges.text(), len(self.document.pages))
        except ViewerError as exc:
            self.message.emit(str(exc))
            return
        self.split_requested.emit(groups, self.each_range.isChecked())

    def _add_files(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(self, "Add files to merge", "", MERGE_FILTER)
        for value in paths:
            self.add_merge_file(value)

    def add_merge_file(self, value: str) -> None:
        item = QListWidgetItem(icons.icon("doc", theme.MUTED), Path(value).name)
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setToolTip(value)
        self.merge_list.addItem(item)

    def _remove_files(self) -> None:
        for item in self.merge_list.selectedItems():
            self.merge_list.takeItem(self.merge_list.row(item))

    def _merge_clicked(self) -> None:
        paths = self.merge_list.paths()
        if len(paths) < 2:
            self.message.emit("Add at least two files to merge.")
            return
        self.merge_requested.emit(paths)


class ProtectionPage(QWidget):
    unprotect_requested = Signal()
    owner_password_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.document = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 24, 14)
        outer.setSpacing(6)
        outer.addWidget(widgets.eyebrow("Protection & compatibility"))
        self.heading = widgets.title("No document open")
        outer.addWidget(self.heading)
        self.subtitle = widgets.body("Open a document to see its protection status and anything James Viewer cannot reproduce.", wrap=False)
        outer.addWidget(self.subtitle)
        outer.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(4)

        card = widgets.Card()
        layout = card.inner_layout()
        layout.addWidget(widgets.title("Password", 17))
        self.state = widgets.body("")
        layout.addWidget(self.state)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.owner_button = QPushButton("Enter owner password…")
        self.owner_button.clicked.connect(self.owner_password_requested)
        self.unprotect_button = widgets.primary_button("Save Unprotected Copy…")
        self.unprotect_button.clicked.connect(self.unprotect_requested)
        buttons.addStretch(1)
        buttons.addWidget(self.owner_button)
        buttons.addWidget(self.unprotect_button)
        layout.addLayout(buttons)
        row.addWidget(card, 2)

        notes_card = widgets.Card()
        notes_layout = notes_card.inner_layout()
        notes_layout.addWidget(widgets.title("Compatibility notes", 17))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self.notes = QVBoxLayout(holder)
        self.notes.setContentsMargins(0, 6, 8, 6)
        self.notes.setSpacing(10)
        scroll.setWidget(holder)
        notes_layout.addWidget(scroll, 1)
        row.addWidget(notes_card, 3)
        outer.addLayout(row, 1)
        self.set_document(None)

    def set_document(self, document) -> None:
        self.document = document
        while self.notes.count():
            item = self.notes.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if document is None:
            self.heading.setText("No document open")
            self.subtitle.setText("Open a document to see its protection status and anything James Viewer cannot reproduce.")
            self.state.setText("")
            self.unprotect_button.setEnabled(False)
            self.owner_button.setVisible(False)
            return
        self.heading.setText(document.name)
        if document.family == "markdown":
            self.subtitle.setText(f"Markdown file with {len(document.page.headings)} headings")
        else:
            self.subtitle.setText(f"{document.format.upper()} file with {len(document.pages)} {document.noun}s")
        access = document.access
        needs_owner = False
        if document.family == "pdf" and access.encrypted:
            limits = [name for name, ok in (("copying text", access.can_extract), ("changing pages", access.can_assemble), ("editing", access.can_modify)) if not ok]
            if access.owner_verified:
                text = "Protected. The owner password was entered, so an unprotected copy is authorized."
            elif limits:
                text = "Protected. Its author restricts " + ", ".join(limits) + ". An unprotected copy needs the owner password."
                needs_owner = True
            else:
                text = "Protected with a password that you entered correctly. It has no other restrictions."
        elif document.protected:
            text = "Protected with a password that you entered correctly."
        elif document.deck is not None and document.deck.modify_protected:
            text = "Marked read-only by its author with a modify password. James Viewer does not edit or unlock it."
        else:
            text = "Not password protected."
        self.state.setText(text)
        self.owner_button.setVisible(needs_owner)
        may_save = document.protected and (document.family != "pdf" or access.may_remove_protection)
        self.unprotect_button.setEnabled(may_save)
        if not document.notes:
            self.notes.addWidget(widgets.body("Nothing to report for this file."))
        for note in document.notes:
            title = QLabel(note.title)
            title.setFont(theme.sans(13, theme.QFont.Weight.DemiBold))
            title.setStyleSheet(f"color: {theme.INK}; background: transparent;")
            title.setWordWrap(True)
            detail = widgets.body(note.detail, 12)
            detail.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
            self.notes.addWidget(title)
            self.notes.addWidget(detail)
        self.notes.addStretch(1)


__all__ = ["HomePage", "ProtectionPage", "SplitMergePage", "math", "QPainterPath", "QSizePolicy"]
