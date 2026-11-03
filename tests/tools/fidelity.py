"""Compare James Viewer's rendering of real decks with images exported by a real PowerPoint.

  fidelity.py <deck.pptx> <reference folder> <output folder>

Scores use a blurred, downscaled grayscale copy, so a one-pixel text shift counts for little and a
missing picture or a wrong line break counts for a lot. 100 means identical.
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("JAMES_VIEWER_DATA_DIR", str(ROOT / ".build" / "data-fidelity"))

import pypdfium2
from PIL import Image, ImageChops, ImageFilter, ImageStat

from james_viewer.core.office_engine import OfficeEngine
from james_viewer.core.pptx_engine import SlideSpec, read_deck_info


def signature(image: Image.Image) -> Image.Image:
    return image.convert("L").filter(ImageFilter.GaussianBlur(3)).resize((320, 180), Image.Resampling.BILINEAR)


def main() -> None:
    deck, reference, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    info = read_deck_info(deck)
    engine = OfficeEngine()
    started = time.perf_counter()
    scores = []
    try:
        pdf = engine.render_specs(str(deck), [SlideSpec(str(deck), i) for i in range(len(info.slides))], out, label="full")
    finally:
        engine.close()
    seconds = time.perf_counter() - started
    document = pypdfium2.PdfDocument(str(pdf))
    for index in range(len(document)):
        page = document[index]
        ours = page.render(scale=1600 / page.get_width()).to_pil().convert("RGB")
        theirs = Image.open(reference / f"slide-{index + 1:03d}.png").convert("RGB").resize(ours.size)
        difference = ImageStat.Stat(ImageChops.difference(signature(ours), signature(theirs))).mean[0]
        scores.append((round(100 - difference / 255 * 100, 2), index + 1))
        page.close()
    document.close()
    scores.sort()
    average = sum(score for score, _slide in scores) / len(scores)
    result = {
        "deck": deck.name,
        "slides": len(scores),
        "render_seconds": round(seconds, 1),
        "average": round(average, 2),
        "worst": scores[:8],
        "below_97": sum(1 for score, _slide in scores if score < 97),
    }
    print(json.dumps(result, ensure_ascii=False))
    for score, slide in scores[:4]:
        page = pypdfium2.PdfDocument(str(pdf))[slide - 1]
        ours = page.render(scale=800 / page.get_width()).to_pil().convert("RGB")
        theirs = Image.open(reference / f"slide-{slide:03d}.png").convert("RGB").resize(ours.size)
        pair = Image.new("RGB", (ours.width * 2 + 12, ours.height), (92, 157, 114))
        pair.paste(ours, (0, 0))
        pair.paste(theirs, (ours.width + 12, 0))
        pair.save(out / f"worst-{slide:03d}-folio-left-powerpoint-right.png")


if __name__ == "__main__":
    main()
