"""Where James Viewer finds its bundled parts and keeps its temporary files."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from .. import APP_NAME


def app_dir() -> Path:
    """The folder holding James Viewer.exe, or the project root when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def office_dir() -> Path | None:
    override = os.environ.get("JAMES_VIEWER_OFFICE_DIR")
    candidates = [Path(override)] if override else []
    candidates += [app_dir() / "office", app_dir() / ".build" / "office"]
    for candidate in candidates:
        if (candidate / "program" / "soffice.exe").exists():
            return candidate
    return None


def worker_script() -> Path:
    if getattr(sys, "frozen", False):
        return resource_dir() / "james_viewer" / "core" / "office_worker.py"
    return Path(__file__).with_name("office_worker.py")


def data_dir() -> Path:
    override = os.environ.get("JAMES_VIEWER_DATA_DIR")
    root = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_root() -> Path:
    path = data_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Session:
    """A private scratch folder for this run. Decrypted content only ever lives here."""

    def __init__(self):
        self._purge_stale()
        self.path = session_root() / f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True)
        self._counter = 0

    def new_dir(self, prefix: str = "item") -> Path:
        self._counter += 1
        path = self.path / f"{prefix}-{self._counter:05d}"
        path.mkdir()
        return path

    def close(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    @staticmethod
    def _purge_stale() -> None:
        """Remove folders left by runs that ended without cleaning up."""
        for entry in session_root().iterdir():
            pid_text = entry.name.split("-", 1)[0]
            if pid_text.isdigit() and not _process_alive(int(pid_text)):
                shutil.rmtree(entry, ignore_errors=True)


def _process_alive(pid: int) -> bool:
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def trim_cache(limit_bytes: int = 1_500_000_000) -> None:
    """Keep the render cache bounded, removing the least recently used entries first."""
    entries = []
    total = 0
    for entry in cache_dir().iterdir():
        if not entry.is_dir():
            continue
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        entries.append((entry.stat().st_mtime, size, entry))
        total += size
    for _mtime, size, entry in sorted(entries, key=lambda item: item[0]):
        if total <= limit_bytes:
            break
        shutil.rmtree(entry, ignore_errors=True)
        total -= size


def unique_path(path: Path) -> Path:
    """`name.ext`, or `name (2).ext` and so on if that already exists."""
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
