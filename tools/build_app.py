"""Build James Viewer.exe, add the office bundle, and create the portable zip.

  python tools/build_app.py

The output goes to dist/<version>/, so a copy of an older version that is still running is left alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build"
VERSION = "1.1.1"
DIST = ROOT / "dist" / VERSION / "x64"

# Parts of the packaged Qt that James Viewer does not load.
QT_REMOVE = [
    "opengl32sw.dll", "Qt6Network.dll", "Qt6Pdf.dll", "Qt6Qml*.dll", "Qt6Quick*.dll", "Qt6OpenGL*.dll",
    "Qt6VirtualKeyboard.dll", "plugins/networkinformation", "plugins/tls", "plugins/generic",
    "plugins/imageformats/qicns.dll", "plugins/imageformats/qpdf.dll", "plugins/imageformats/qtga.dll",
    "plugins/imageformats/qtiff.dll", "plugins/imageformats/qwbmp.dll", "plugins/iconengines",
    "plugins/platforminputcontexts", "plugins/platforms/qdirect2d.dll", "plugins/platforms/qminimal.dll",
    "plugins/platforms/qoffscreen.dll", "translations",
]


def size_of(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True, cwd=ROOT, env={**os.environ, "JAMES_VIEWER_ROOT": str(ROOT), "PYTHONIOENCODING": "utf-8"})


def main() -> None:
    python = sys.executable
    office = BUILD / "office"
    if not (office / "program" / "soffice.exe").exists():
        raise SystemExit("Build the office bundle first with tools/build_office.py.")

    run(python, str(ROOT / "tools" / "make_icon.py"), str(BUILD / "icon" / "james_viewer.ico"))
    shutil.rmtree(DIST, ignore_errors=True)
    run(
        python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
        str(ROOT / "tools" / "james_viewer.spec"),
    )
    app = DIST / "James Viewer"
    internal = app / "_internal"
    qt = internal / "PySide6"
    for pattern in QT_REMOVE:
        for match in qt.glob(pattern):
            if match.is_dir():
                shutil.rmtree(match)
            else:
                match.unlink()
    print(f"app without office: {size_of(app) / 1e6:.0f} MB", flush=True)

    shutil.copytree(office, app / "office")
    shutil.copyfile(ROOT / "README.md", app / "README.md")
    licenses = app / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs" / "THIRD_PARTY.md", licenses / "THIRD_PARTY.md")
    shutil.copyfile(ROOT / "tests" / "fixtures" / "rich.pptx", licenses / "self-check.pptx")
    shutil.copyfile(ROOT / "tools" / "Self-check.cmd", app / "Self-check.cmd")
    print(f"app with office: {size_of(app) / 1e6:.0f} MB", flush=True)

    archive = DIST.parent / f"James-Viewer-{VERSION}-win-x64-portable.zip"
    started = time.perf_counter()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for item in sorted(app.rglob("*")):
            if item.is_file():
                output.write(item, Path("James Viewer") / item.relative_to(app))
    print(f"zip: {archive.stat().st_size / 1e6:.0f} MB in {time.perf_counter() - started:.0f}s at {archive}", flush=True)


if __name__ == "__main__":
    main()
