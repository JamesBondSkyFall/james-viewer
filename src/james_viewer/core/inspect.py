"""Report what a document contains that James Viewer cannot reproduce, in plain English."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from .office_engine import _FONT_PAIRS
from .opc import Package
from .pptx_engine import _presentation_slides, scan_fonts


@dataclass(frozen=True)
class Note:
    title: str
    detail: str
    important: bool = True


def _count(pattern: bytes, blobs: list[bytes]) -> int:
    expression = re.compile(pattern)
    return sum(len(expression.findall(blob)) for blob in blobs)


def inspect_pptx(path: str | Path) -> list[Note]:
    notes: list[Note] = []
    with Package(path) as package:
        pres, root, slides = _presentation_slides(package)
        blobs = [part.blob for _node, part in slides]
        animated = sum(1 for blob in blobs if b"<p:timing" in blob and b"<p:par>" in blob)
        transitions = sum(1 for blob in blobs if b"<p:transition" in blob)
        media = _count(rb"<a:(?:videoFile|audioFile)\b", blobs) + _count(rb"<p14:media\b", blobs)
        ole = _count(rb"<p:oleObj\b", blobs)
        smartart = _count(rb"<dgm:relIds\b", blobs)
        modern_charts = sum(1 for blob in blobs if b"/2014/chartex" in blob)
        models = _count(rb"<am3d:model3d\b", blobs)
        ink = _count(rb"<p14:contentPart\b", blobs)
        charts = _count(rb'/drawingml/2006/chart"', blobs)
        macros = any(part.name.lower().endswith("vbaproject.bin") for part in package.parts.values())
        embedded_fonts = root.find("{http://schemas.openxmlformats.org/presentationml/2006/main}embeddedFontLst") is not None

    if animated:
        notes.append(Note("Animations are not played", f"{animated} slide(s) have animations. They appear in their final state, with everything visible."))
    if transitions:
        notes.append(Note("Slide transitions are not played", f"{transitions} slide(s) have transitions. Slides change instantly instead."))
    if media:
        notes.append(Note("Audio and video do not play", f"{media} media item(s) show only as their still picture."))
    if ole:
        notes.append(Note("Embedded objects are pictures", f"{ole} embedded object(s) show their saved preview and cannot be opened."))
    if modern_charts:
        notes.append(Note("Some newer chart types may be missing", f"{modern_charts} slide(s) use charts such as waterfall or treemap, which the office engine may show as a blank or simplified picture."))
    if models:
        notes.append(Note("3D models show as still pictures", f"{models} 3D model(s) cannot be rotated."))
    if ink:
        notes.append(Note("Ink may not appear", f"{ink} ink drawing(s) may be simplified or missing."))
    if smartart:
        notes.append(Note("SmartArt text cannot be edited", f"{smartart} SmartArt graphic(s) are shown from their saved drawing. Their text is read-only here.", False))
    if charts:
        notes.append(Note("Chart text cannot be edited", f"{charts} chart(s) are redrawn by the office engine, so label positions can differ slightly from PowerPoint.", False))
    if macros:
        notes.append(Note("Macros are not run", "This file contains macros. James Viewer never runs them.", False))
    if embedded_fonts:
        notes.append(Note("Embedded fonts are not used", "This file embeds fonts. Installed or substitute fonts are used instead.", False))
    return notes


def inspect_ppt() -> list[Note]:
    return [
        Note("Animations, transitions and media are not played", "Slides in PowerPoint 97-2003 files appear in their final state, and audio and video show only as still pictures."),
        Note("Editing converts to PPTX", "Editing a .ppt file first creates a PPTX version. Your original .ppt stays unchanged.", False),
    ]


def font_notes(path: str | Path, installed: set[str]) -> list[Note]:
    """Name each font the deck asks for that this PC lacks, and what is used instead."""
    wanted = scan_fonts(path)
    known = {name.lower() for name in installed}
    missing = sorted(name for name in wanted if name.lower() not in known)
    if not missing:
        return []
    pieces = []
    for name in missing[:12]:
        target = _FONT_PAIRS.get(name)
        pieces.append(f"{name} → {target}" if target else f"{name} → automatic substitute")
    if len(missing) > 12:
        pieces.append(f"and {len(missing) - 12} more")
    return [Note("Fonts were substituted", "; ".join(pieces) + ". Line breaks can differ from the original.", False)]


def inspect_pdf(path: str | Path, password: str | None) -> list[Note]:
    notes: list[Note] = []
    try:
        with pikepdf.open(path, password=password or "") as pdf:
            root = pdf.Root
            acroform = root.get("/AcroForm")
            if acroform is not None and "/XFA" in acroform:
                notes.append(Note("XFA forms are not supported", "This PDF uses dynamic XFA forms, so form content may be missing."))
            elif acroform is not None and len(acroform.get("/Fields", [])) > 0:
                notes.append(Note("Forms are shown but not filled", "Form fields display their saved values and cannot be typed into.", False))
            names = root.get("/Names")
            if (names is not None and "/JavaScript" in names) or (
                isinstance(root.get("/OpenAction"), pikepdf.Dictionary) and root.OpenAction.get("/S") == pikepdf.Name.JavaScript
            ):
                notes.append(Note("Scripts are not run", "This PDF contains JavaScript. James Viewer never runs it.", False))
            if names is not None and "/EmbeddedFiles" in names:
                notes.append(Note("Attachments are not shown", "This PDF has attached files, which James Viewer does not list or open.", False))
            subtypes: set[str] = set()
            for page in pdf.pages:
                for annotation in page.obj.get("/Annots", []):
                    subtypes.add(str(annotation.get("/Subtype", "")))
            if subtypes & {"/Movie", "/Sound", "/Screen", "/RichMedia", "/3D"}:
                notes.append(Note("Rich media does not play", "Video, sound or 3D content in this PDF is not played."))
    except (pikepdf.PdfError, ValueError, TypeError, AttributeError):
        pass
    return notes
