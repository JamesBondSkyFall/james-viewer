"""Run a packaged James Viewer self-check here and print its report.

  run_self_check.py <folder holding James Viewer.exe> <empty data folder>
"""

import ctypes
import os
import subprocess
import sys
from pathlib import Path

app, data = Path(sys.argv[1]), Path(sys.argv[2])
buffer = ctypes.create_unicode_buffer(600)
ctypes.windll.shell32.SHGetFolderPathW(0, 0x10, 0, 0, buffer)
report = Path(buffer.value) / "James Viewer self-check.txt"
report.unlink(missing_ok=True)
code = subprocess.run([str(app / "James Viewer.exe"), "--self-check", "--quiet"], env={**os.environ, "JAMES_VIEWER_DATA_DIR": str(data)}, timeout=240).returncode
print("exit code", code)
print(report.read_text(encoding="utf-8") if report.exists() else "no report was written")
report.unlink(missing_ok=True)
