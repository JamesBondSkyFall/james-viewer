"""Write edited, merged and protected files with the final code, for an outside app to open.

  final_outputs.py <output folder> <deck1> <deck2> <deck3>
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from james_viewer.core.pptx_engine import SlideSpec, build_deck, list_text_blocks, read_deck_info
from james_viewer.core.security import decrypt_office, decrypt_ppt, encrypt_pptx

PASSWORD = "Folio-Test-2026"


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    decks = [str(Path(value).resolve()) for value in sys.argv[2:5]]
    first, second, third = decks
    count = len(read_deck_info(first).slides)

    build_deck(first, [SlideSpec(first, i) for i in reversed(range(count))], out / "reversed.pptx")
    build_deck(first, [SlideSpec(first, 0), SlideSpec(first, 0), SlideSpec(first, 1)], out / "duplicated.pptx")
    build_deck(first, [SlideSpec(first, i) for i in range(1, count, 2)], out / "split-every-other.pptx")

    blocks = list_text_blocks(first, SlideSpec(first, 4))
    edit = (blocks[0].index, blocks[0].text + "\n新增一行 Added line")
    build_deck(first, [SlideSpec(first, 4, edits=(edit,)), SlideSpec(first, 5)], out / "text-edited.pptx")

    info = read_deck_info(first)
    layout = info.slides[0].layout_part
    build_deck(first, [SlideSpec(first, 0), SlideSpec(first, -1, layout=layout)], out / "new-slide.pptx")

    specs = [SlideSpec(path, i) for path in decks for i in range(len(read_deck_info(path).slides))]
    report = build_deck(first, specs, out / "merged-three-decks.pptx")
    print("merged slides", len(specs), "warnings", report.warnings)

    fixtures = ROOT / "tests" / "fixtures"
    mixed = [SlideSpec(str(fixtures / "rich.pptx"), 4), SlideSpec(str(fixtures / "standard43.pptx"), 4), SlideSpec(str(fixtures / "standard43.pptx"), 3)]
    print("mixed size warnings", build_deck(str(fixtures / "rich.pptx"), mixed, out / "merged-different-sizes.pptx").warnings)

    encrypt_pptx(fixtures / "rich.pptx", PASSWORD, out / "re-protected.pptx")
    decrypt_office(fixtures / "rich-locked.pptx", PASSWORD, out / "unprotected-from-pptx.pptx")
    decrypt_ppt(fixtures / "rich-locked.ppt", PASSWORD, out / "unprotected-from-ppt.ppt")
    for item in sorted(out.iterdir()):
        print(f"{item.stat().st_size:>10}  {item.name}")


if __name__ == "__main__":
    main()
