"""Small styled building blocks shared by the pages."""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import icons, theme


def eyebrow(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setFont(theme.sans(10, theme.QFont.Weight.DemiBold, 2.0))
    label.setStyleSheet(f"color: {theme.MUTED};")
    return label


def title(text: str, size: int = 27) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.sans(size, theme.QFont.Weight.DemiBold, 0.3))
    label.setStyleSheet(f"color: {theme.INK};")
    return label


def body(text: str, size: int = 13, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.sans(size))
    label.setStyleSheet(f"color: {theme.MUTED};")
    label.setWordWrap(wrap)
    return label


def numeral(text: str, size: int = 46, color: str = theme.ACCENT_LIGHT) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.numeral(size))
    label.setStyleSheet(f"color: {color};")
    return label


def primary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("primary", True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def tool_button(icon_name: str, tip: str, checkable: bool = False, color: str = theme.MUTED) -> QToolButton:
    button = QToolButton()
    button.setIcon(icons.icon(icon_name, color))
    button.setIconSize(QSize(20, 20))
    button.setToolTip(tip)
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class Card(QFrame):
    """A rounded paper panel with soft depth."""

    def __init__(self, parent=None, fill: str = theme.PAPER, radius: int = 14, depth: bool = True):
        super().__init__(parent)
        self.fill = fill
        self.radius = radius
        self.depth = depth
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(9, 5, -9, -13)
        if self.depth:
            theme.soft_shadow(painter, rect, self.radius, 10, 0.07, 4)
        painter.setPen(QColor(theme.LINE))
        painter.setBrush(QColor(self.fill))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)

    def inner_layout(self) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 36)
        layout.setSpacing(10)
        return layout


class NavItem(QAbstractButton):
    def __init__(self, icon_name: str, label: str, number: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.label = label
        self.number = number
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        self._hover = False

    def enterEvent(self, _event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT_SOFT))
            painter.drawRoundedRect(rect, 10, 10)
            bar = QRectF(0, 8, 3.5, rect.height() - 16)
            painter.setBrush(theme.accent_gradient(bar, horizontal=False))
            painter.drawRoundedRect(bar, 1.7, 1.7)
        elif self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT_FAINT))
            painter.drawRoundedRect(rect, 10, 10)
        tone = theme.ACCENT_DARK if self.isChecked() else theme.MUTED
        icons.draw_icon(painter, self.icon_name, QRectF(14, 14, 20, 20), tone)
        painter.setFont(theme.sans(14, theme.QFont.Weight.DemiBold if self.isChecked() else theme.QFont.Weight.Normal))
        painter.setPen(QColor(theme.INK if self.isChecked() else theme.MUTED))
        painter.drawText(rect.adjusted(46, 0, -30, 0), Qt.AlignmentFlag.AlignVCenter, self.label)
        painter.setFont(theme.sans(10, theme.QFont.Weight.Normal, 0.8))
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.number)


class BrandMark(QWidget):
    def __init__(self, size: int = 42, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        icons.draw_mark(painter, QRectF(self.rect()))


class Toast(QLabel):
    """A short confirmation floating above the bottom edge."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.INK}; color: #f4f8f5; border-radius: 8px; padding: 10px 18px;")
        self.setFont(theme.sans(13))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.left_inset = 0
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"opacity", self)
        self._animation.setDuration(180)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade)
        self.hide()

    def show_message(self, text: str, milliseconds: int = 3200) -> None:
        self.setText(QFontMetrics(self.font()).elidedText(text, Qt.TextElideMode.ElideMiddle, 760))
        self.adjustSize()
        parent = self.parentWidget()
        self.move((parent.width() - self.width() + self.left_inset) // 2, parent.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._animation.stop()
        self._animation.setStartValue(self._effect.opacity())
        self._animation.setEndValue(1.0)
        self._animation.start()
        self._timer.start(milliseconds)

    def _fade(self) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._effect.opacity())
        self._animation.setEndValue(0.0)
        self._animation.start()
        QTimer.singleShot(200, self.hide)


class Banner(QFrame):
    """A one-line notice under the toolbar, for things the user should know about this file."""

    details_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"Banner {{ background: {theme.ACCENT_FAINT}; border-bottom: 1px solid {theme.LINE}; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 7, 12, 7)
        self.label = QLabel()
        self.label.setFont(theme.sans(12))
        self.label.setStyleSheet(f"color: {theme.INK}; background: transparent;")
        self.details = QPushButton("Details")
        self.details.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {theme.ACCENT_DARK}; font-weight: 600; padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {theme.INK}; }}"
        )
        self.details.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details.clicked.connect(self.details_requested)
        close = tool_button("x", "Dismiss")
        close.clicked.connect(self.hide)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.details)
        layout.addWidget(close)
        self.hide()

    def show_text(self, text: str) -> None:
        self.label.setText(text)
        self.show()


class PasswordDialog(QDialog):
    def __init__(self, name: str, *, wrong: bool, owner: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Password required")
        self.setModal(True)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(10)
        layout.addWidget(eyebrow("Protected document" if not owner else "Owner password"))
        layout.addWidget(title(name if len(name) < 42 else name[:39] + "…", 19))
        prompt = (
            "Enter the owner password set by this file's author to authorize the action."
            if owner else "Enter the password to open this file. James Viewer only ever tries what you type here."
        )
        layout.addWidget(body(prompt))
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText("Password")
        layout.addSpacing(4)
        layout.addWidget(self.field)
        self.error = QLabel("That password is not correct. Try again.")
        self.error.setStyleSheet(f"color: {theme.DANGER};")
        self.error.setVisible(wrong)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        confirm = primary_button("Open" if not owner else "Authorize")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(confirm)
        layout.addSpacing(6)
        layout.addLayout(row)
        self.field.setFocus()


def ask(parent, heading: str, text: str, confirm: str, cancel: str = "Cancel", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("James Viewer")
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setText(f"<b>{heading}</b>")
    box.setInformativeText(text)
    accept = box.addButton(confirm, QMessageBox.ButtonRole.AcceptRole)
    reject = box.addButton(cancel, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(reject if danger else accept)
    if danger:
        accept.setProperty("danger", True)
    else:
        accept.setProperty("primary", True)
    box.exec()
    return box.clickedButton() is accept


def ask_text(parent, heading: str, label: str) -> str | None:
    """A few lines of text from the reader, or None if they cancel."""
    from PySide6.QtWidgets import QInputDialog

    text, accepted = QInputDialog.getMultiLineText(parent, heading, label)
    return text.strip() if accepted and text.strip() else None


def tell(parent, heading: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("James Viewer")
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setText(f"<b>{heading}</b>")
    box.setInformativeText(text)
    box.addButton("OK", QMessageBox.ButtonRole.AcceptRole).setProperty("primary", True)
    box.exec()


__all__ = [
    "Banner", "BrandMark", "Card", "NavItem", "PasswordDialog", "Toast", "ask", "body", "eyebrow",
    "numeral", "primary_button", "tell", "title", "tool_button", "QSizePolicy", "QLinearGradient",
]
