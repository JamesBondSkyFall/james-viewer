# PyInstaller recipe. Run through tools/build_app.py, which sets the paths below.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(os.environ["JAMES_VIEWER_ROOT"])

# Modules James Viewer never uses. Leaving them out keeps startup fast and the package small.
EXCLUDES = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL", "PySide6.QtPdf",
    "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtDBus", "PySide6.QtHelp",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtSvgWidgets", "PySide6.QtPrintSupport",
    "PySide6.QtConcurrent", "tkinter", "unittest", "test", "pptx", "xlsxwriter", "numpy", "pytest",
    "setuptools", "pip", "PIL",
]

# The formula renderer needs its bundled math font, and Pygments finds its languages by name.
DATAS = [
    (str(root / "src" / "james_viewer" / "core" / "office_worker.py"), "james_viewer/core"),
    *collect_data_files("ziamath"),
    *collect_data_files("ziafont"),
    *collect_data_files("latex2mathml"),
]
HIDDEN = [
    "james_viewer.core.office_engine",
    "james_viewer.ui.mainwindow",
    "PySide6.QtSvg",
    *collect_submodules("pygments.lexers"),
    *collect_submodules("pygments.formatters"),
    *collect_submodules("pygments.styles"),
    *collect_submodules("mdit_py_plugins"),
    *collect_submodules("markdown_it"),
]

analysis = Analysis(
    [str(root / "src" / "james_viewer" / "__main__.py")],
    pathex=[str(root / "src")],
    datas=DATAS,
    hiddenimports=HIDDEN,
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="James Viewer",
    console=False,
    icon=str(root / ".build" / "icon" / "james_viewer.ico"),
    version=str(root / "tools" / "version_info.txt"),
    upx=False,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="James Viewer", upx=False)
