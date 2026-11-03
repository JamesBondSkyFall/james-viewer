"""Time the first open and the reopen of a Markdown file, each in a fresh process.

  md_timing.py <file.md> <first|again>
"""

import os
import sys
import time
from pathlib import Path

started = time.perf_counter()
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("JAMES_VIEWER_DATA_DIR", str(ROOT / ".build" / "data-timing"))

from james_viewer.core import paths
from james_viewer.core.markdown_engine import parse_markdown

path = Path(sys.argv[1])
if sys.argv[2] == "first":
    for saved in (paths.cache_dir() / "markdown").glob("*.json") if (paths.cache_dir() / "markdown").exists() else []:
        saved.unlink()
before = time.perf_counter()
page = parse_markdown(path)
print(f"{sys.argv[2]}: parse {time.perf_counter() - before:.3f}s, process total {time.perf_counter() - started:.3f}s, formulas {sum(a.kind == 'math' for a in page.assets)}, ziamath loaded: {'ziamath' in sys.modules}, markdown_it loaded: {'markdown_it' in sys.modules}")
