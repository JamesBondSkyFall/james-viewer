"""Shared test setup. Tests drive the real window on the real Windows platform."""

import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("JAMES_VIEWER_DATA_DIR", tempfile.mkdtemp(prefix="james-viewer-test-data-"))
if "JAMES_VIEWER_OFFICE_DIR" not in os.environ:
    trimmed = ROOT / ".build" / "office" / "program" / "soffice.exe"
    os.environ["JAMES_VIEWER_OFFICE_DIR"] = str(trimmed.parents[1] if trimmed.exists() else ROOT / ".build" / "lo-full")

FIXTURES = ROOT / "tests" / "fixtures"
PASSWORD = "Folio-Test-2026"
OWNER_PASSWORD = "Owner-Test-2026"


def pump(seconds: float, until=None) -> bool:
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until is None


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from james_viewer.ui import theme

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(theme.sans(13))
    app.setStyleSheet(theme.STYLE_SHEET)
    yield app


class Harness:
    """Opens documents and answers dialogs, so a test reads like a user session."""

    def __init__(self, window, monkeypatch):
        from james_viewer.ui import widgets

        self.window = window
        self.messages: list[str] = []
        self.told: list[tuple[str, str]] = []
        self.passwords: list[str] = []
        self.password_prompts = 0
        self.cancelled = 0
        self.answers: list[bool] = []
        self.asked: list[str] = []
        window.toast.show_message = lambda text, milliseconds=0: self.messages.append(text)
        monkeypatch.setattr(widgets, "tell", lambda parent, heading, text: self.told.append((heading, text)))
        monkeypatch.setattr(widgets, "ask", self._ask)
        harness = self

        class FakePasswordDialog:
            def __init__(self, name, *, wrong, owner=False, parent=None):
                harness.password_prompts += 1
                self.wrong = wrong
                self.owner = owner
                self.field = self
                self.typed = ""

            def exec(self):
                from PySide6.QtWidgets import QDialog

                if not harness.passwords:
                    harness.cancelled += 1
                    return QDialog.DialogCode.Rejected
                self.typed = harness.passwords.pop(0)
                return QDialog.DialogCode.Accepted

            def text(self):
                return self.typed

        monkeypatch.setattr(widgets, "PasswordDialog", FakePasswordDialog)

    def _ask(self, parent, heading, text, confirm, cancel="Cancel", danger=False):
        self.asked.append(heading)
        return self.answers.pop(0) if self.answers else False

    def open(self, path, timeout: float = 240.0):
        before = len(self.window.views)
        told_before = len(self.told)
        cancelled_before = self.cancelled
        self.window.open_paths([str(path)])
        pump(timeout, lambda: len(self.window.views) > before or len(self.told) > told_before or self.cancelled > cancelled_before)
        if len(self.window.views) <= before:
            return None
        document = self.window.views[-1].document
        assert pump(timeout, lambda: not document.preparing), "document stayed in preparing state"
        pump(0.05)
        return self.window.views[-1]

    def settle(self, document, timeout: float = 120.0) -> None:
        """Wait until every page in the current state has its render."""
        pump(0.5)
        pump(timeout, lambda: not document._refresh_timer.isActive() and all(document._key_at(i) in document.workspace.renders for i in range(len(document.pages))))
        pump(0.1)

    def close_all(self) -> None:
        for view in list(self.window.views):
            view.document._saved_pages = view.document.pages
            view.document._saved_format = view.document.format
            self.window.close_tab(self.window.views.index(view))


@pytest.fixture(scope="session")
def main_window(qapp):
    from james_viewer.ui.mainwindow import MainWindow

    window = MainWindow()
    window.show()
    pump(0.3)
    yield window
    window.close()


@pytest.fixture
def ui(main_window, monkeypatch):
    harness = Harness(main_window, monkeypatch)
    yield harness
    harness.close_all()


@pytest.fixture
def work(tmp_path):
    return tmp_path
