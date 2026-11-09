"""PDF annotations: highlight, underline, notes, text boxes and ink.

Each annotation is a standard PDF annotation with its own appearance stream, so Acrobat, browsers
and James Viewer all show it the same way, and Acrobat can still edit it. Text boxes are drawn
as a picture of the text, because embedding a Chinese font in every text box would make a small
note cost megabytes. The text itself is kept in the annotation's contents.

An annotation is a plain dictionary, stored as JSON so a page's annotations stay hashable:
  {"type": "highlight" | "underline", "rects": [[left, bottom, right, top], ...]}
  {"type": "note", "x": .., "y": .., "text": ".."}
  {"type": "text", "rect": [left, bottom, right, top], "text": "..", "size": 12}
  {"type": "ink", "paths": [[[x, y], ...], ...], "width": 2}
Coordinates are PDF points with the origin at the page's bottom left.
"""

from __future__ import annotations

import datetime
import json
import uuid
import zlib
from pathlib import Path
from typing import Callable

import pikepdf

COLOURS = {
    "highlight": (1.0, 0.86, 0.25),
    "underline": (0.16, 0.44, 0.85),
    "note": (1.0, 0.82, 0.2),
    "ink": (0.85, 0.18, 0.18),
}
TextPainter = Callable[[str, float, float, float], tuple[bytes, int, int]]  # text, size, width, height -> RGBA


def _rgb(colour: tuple[float, float, float]) -> str:
    return " ".join(f"{part:.3f}" for part in colour)


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _box(rects: list[list[float]], pad: float = 0.0) -> list[float]:
    return [
        min(r[0] for r in rects) - pad, min(r[1] for r in rects) - pad,
        max(r[2] for r in rects) + pad, max(r[3] for r in rects) + pad,
    ]


def _appearance(pdf: pikepdf.Pdf, rect: list[float], content: str, resources: dict | None = None) -> pikepdf.Stream:
    stream = pikepdf.Stream(pdf, content.encode("latin-1"))
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Form
    stream.BBox = pikepdf.Array(rect)
    stream.Resources = pikepdf.Dictionary(resources or {})
    return stream


def _common(kind: str, rect: list[float], colour: tuple[float, float, float] | None = None) -> pikepdf.Dictionary:
    stamp = datetime.datetime.now().strftime("D:%Y%m%d%H%M%S")
    entry = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name("/" + kind),
        Rect=pikepdf.Array(rect),
        F=4,  # print it too
        M=pikepdf.String(stamp),
        NM=pikepdf.String(uuid.uuid4().hex),
        T=pikepdf.String("James Viewer"),
    )
    if colour is not None:
        entry.C = pikepdf.Array(list(colour))
    return entry


def _marked_text(pdf: pikepdf.Pdf, data: dict) -> pikepdf.Dictionary:
    kind = data["type"]
    rects = [list(map(float, rect)) for rect in data["rects"]]
    colour = COLOURS[kind]
    rect = _box(rects, 1.0)
    quads: list[float] = []
    for left, bottom, right, top in rects:
        quads += [left, top, right, top, left, bottom, right, bottom]
    if kind == "highlight":
        # Multiply keeps the text readable through the colour, as a highlighter pen does.
        drawing = "/Mark gs " + _rgb(colour) + " rg " + " ".join(
            f"{_number(l)} {_number(b)} {_number(r - l)} {_number(t - b)} re" for l, b, r, t in rects
        ) + " f"
        resources = {"/ExtGState": pikepdf.Dictionary(Mark=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, BM=pikepdf.Name.Multiply))}
        appearance = _appearance(pdf, rect, drawing, resources)
        entry = _common("Highlight", rect, colour)
    else:
        lines = []
        for left, bottom, right, top in rects:
            height = top - bottom
            y = bottom + height * 0.08
            lines.append(f"{_number(max(0.6, height * 0.07))} w {_number(left)} {_number(y)} m {_number(right)} {_number(y)} l S")
        appearance = _appearance(pdf, rect, _rgb(colour) + " RG " + " ".join(lines))
        entry = _common("Underline", rect, colour)
    entry.QuadPoints = pikepdf.Array(quads)
    entry.AP = pikepdf.Dictionary(N=pdf.make_indirect(appearance))
    return entry


def _note(pdf: pikepdf.Pdf, data: dict) -> pikepdf.Dictionary:
    x, y = float(data["x"]), float(data["y"])
    rect = [x, y - 20, x + 20, y]
    colour = COLOURS["note"]
    # A small folded note, drawn in the box the reader clicked.
    drawing = (
        f"{_rgb(colour)} rg 0.45 0.35 0.05 RG 0.8 w "
        f"{_number(x + 1)} {_number(y - 19)} 18 18 re B "
        f"0.45 0.35 0.05 RG 1 w "
        f"{_number(x + 5)} {_number(y - 6)} m {_number(x + 15)} {_number(y - 6)} l S "
        f"{_number(x + 5)} {_number(y - 10)} m {_number(x + 15)} {_number(y - 10)} l S "
        f"{_number(x + 5)} {_number(y - 14)} m {_number(x + 12)} {_number(y - 14)} l S"
    )
    entry = _common("Text", rect, colour)
    entry.Contents = pikepdf.String(data.get("text", ""))
    entry.Name = pikepdf.Name.Comment
    entry.Open = False
    entry.AP = pikepdf.Dictionary(N=pdf.make_indirect(_appearance(pdf, rect, drawing)))
    return entry


def _text_box(pdf: pikepdf.Pdf, data: dict, paint: TextPainter | None) -> pikepdf.Dictionary:
    rect = list(map(float, data["rect"]))
    left, bottom, right, top = rect
    size = float(data.get("size", 12))
    entry = _common("FreeText", rect)
    entry.Contents = pikepdf.String(data.get("text", ""))
    entry.DA = pikepdf.String(f"/Helv {_number(size)} Tf 0 0 0 rg")
    entry.BS = pikepdf.Dictionary(W=0)
    if paint is None:
        return entry
    rgba, width_px, height_px = paint(data.get("text", ""), size, right - left, top - bottom)
    rgb = bytearray(width_px * height_px * 3)
    alpha = bytearray(width_px * height_px)
    rgb[0::3], rgb[1::3], rgb[2::3] = rgba[0::4], rgba[1::4], rgba[2::4]
    alpha[:] = rgba[3::4]
    mask = pikepdf.Stream(pdf, zlib.compress(bytes(alpha)))
    mask.Type, mask.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    mask.Width, mask.Height, mask.ColorSpace, mask.BitsPerComponent = width_px, height_px, pikepdf.Name.DeviceGray, 8
    mask.Filter = pikepdf.Name.FlateDecode
    image = pikepdf.Stream(pdf, zlib.compress(bytes(rgb)))
    image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    image.Width, image.Height, image.ColorSpace, image.BitsPerComponent = width_px, height_px, pikepdf.Name.DeviceRGB, 8
    image.Filter = pikepdf.Name.FlateDecode
    image.SMask = pdf.make_indirect(mask)
    drawing = f"q {_number(right - left)} 0 0 {_number(top - bottom)} {_number(left)} {_number(bottom)} cm /Words Do Q"
    resources = {"/XObject": pikepdf.Dictionary(Words=pdf.make_indirect(image))}
    entry.AP = pikepdf.Dictionary(N=pdf.make_indirect(_appearance(pdf, rect, drawing, resources)))
    return entry


def _ink(pdf: pikepdf.Pdf, data: dict) -> pikepdf.Dictionary:
    width = float(data.get("width", 2))
    paths = [[(float(x), float(y)) for x, y in path] for path in data["paths"] if path]
    points = [point for path in paths for point in path]
    rect = [min(p[0] for p in points) - width, min(p[1] for p in points) - width,
            max(p[0] for p in points) + width, max(p[1] for p in points) + width]
    colour = COLOURS["ink"]
    strokes = []
    for path in paths:
        moves = [f"{_number(path[0][0])} {_number(path[0][1])} m"]
        moves += [f"{_number(x)} {_number(y)} l" for x, y in path[1:]] or [f"{_number(path[0][0])} {_number(path[0][1])} l"]
        strokes.append(" ".join(moves) + " S")
    drawing = f"{_rgb(colour)} RG {_number(width)} w 1 J 1 j " + " ".join(strokes)
    entry = _common("Ink", rect, colour)
    entry.InkList = pikepdf.Array([pikepdf.Array([coordinate for point in path for coordinate in point]) for path in paths])
    entry.BS = pikepdf.Dictionary(W=width)
    entry.AP = pikepdf.Dictionary(N=pdf.make_indirect(_appearance(pdf, rect, drawing)))
    return entry


def add_annotations(pdf: pikepdf.Pdf, page: pikepdf.Page, annotations: list[str], paint: TextPainter | None = None) -> None:
    """Add annotations, given as JSON, to a page of an open PDF."""
    made = []
    for text in annotations:
        data = json.loads(text)
        kind = data["type"]
        if kind in ("highlight", "underline"):
            entry = _marked_text(pdf, data)
        elif kind == "note":
            entry = _note(pdf, data)
        elif kind == "text":
            entry = _text_box(pdf, data, paint)
        elif kind == "ink":
            entry = _ink(pdf, data)
        else:
            continue
        entry.P = page.obj
        made.append(pdf.make_indirect(entry))
    existing = list(page.obj.get("/Annots", pikepdf.Array()))
    # A new array, so a duplicated page that shared the old one does not gain these too.
    page.obj.Annots = pdf.make_indirect(pikepdf.Array(existing + made))


def annotated_page(source: str, password: str | None, index: int, annotations: list[str], out: str | Path,
                   paint: TextPainter | None = None) -> None:
    """A one-page PDF of a page with its new annotations, for showing it on screen."""
    with pikepdf.open(source, password=password or "") as original, pikepdf.new() as single:
        single.pages.append(original.pages[index])
        add_annotations(single, single.pages[0], annotations, paint)
        single.save(out)


def page_rotation(source: str, password: str | None, index: int) -> int:
    with pikepdf.open(source, password=password or "") as pdf:
        return int(pdf.pages[index].obj.get("/Rotate", 0)) % 360
