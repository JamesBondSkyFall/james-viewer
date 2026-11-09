"""Word documents: list the text of each paragraph and replace it, keeping the formatting.

Only the document text, and the styles when a font is set everywhere, change. Pictures, headers and
everything else are copied as they were.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .errors import CorruptFile
from .opc import Package
from .pptx_engine import LINE_BREAK

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


# Containers whose paragraphs belong to someone else, or that hold no visible text.
_SKIP = {_w("txbxContent"), _w("pPr"), _w("rPr"), _w("del"), _w("moveFrom"), f"{{{MC}}}Fallback"}
_TEXT_PIECES = {_w("t"), _w("tab"), _w("br"), _w("cr"), _w("noBreakHyphen")}
_FIELDS = {_w("fldChar"), _w("fldSimple"), _w("instrText")}


@dataclass(frozen=True)
class Paragraph:
    index: int
    text: str
    editable: bool
    where: str  # body, table or text box


def _own_nodes(element: etree._Element):
    """Everything inside a paragraph that belongs to it, not to a text box nested in one of its runs."""
    for child in element:
        if not isinstance(child.tag, str) or child.tag in _SKIP:
            continue
        yield child
        yield from _own_nodes(child)


def _paragraphs(body: etree._Element):
    """All paragraphs in reading order, skipping the copy of a text box Word keeps for old readers."""
    for element in body.iter(_w("p")):
        if any(ancestor.tag == f"{{{MC}}}Fallback" for ancestor in element.iterancestors()):
            continue
        yield element


def _text_of(paragraph: etree._Element) -> tuple[str, bool]:
    pieces: list[str] = []
    has_field = False
    for node in _own_nodes(paragraph):
        tag = node.tag
        if tag in _FIELDS:
            has_field = True
        elif tag == _w("t"):
            pieces.append(node.text or "")
        elif tag == _w("tab") and node.getparent().tag == _w("r"):
            pieces.append("\t")
        elif tag in (_w("br"), _w("cr")) and node.get(_w("type")) not in ("page", "column"):
            pieces.append(LINE_BREAK)
        elif tag == _w("noBreakHyphen"):
            pieces.append("-")
    return "".join(pieces), has_field


def _where(paragraph: etree._Element) -> str:
    for ancestor in paragraph.iterancestors():
        if ancestor.tag == _w("txbxContent"):
            return "text box"
        if ancestor.tag == _w("tc"):
            return "table"
    return "body"


def _document_xml(path: str | Path) -> tuple[Package, object, etree._Element]:
    try:
        package = Package(path)
    except Exception as exc:
        raise CorruptFile(f"{Path(path).name} is damaged and cannot be read ({exc}).") from exc
    part = package.main_part()
    root = part.xml()
    if root.find(_w("body")) is None:
        package.close()
        raise CorruptFile(f"{Path(path).name} has no document body.")
    return package, part, root


def read_paragraphs(path: str | Path) -> list[Paragraph]:
    package, _part, root = _document_xml(path)
    try:
        found = []
        for index, element in enumerate(_paragraphs(root.find(_w("body")))):
            text, has_field = _text_of(element)
            found.append(Paragraph(index, text, not has_field, _where(element)))
        return found
    finally:
        package.close()


def _new_pieces(text: str) -> list[etree._Element]:
    pieces = []
    for part in re.split(f"(\t|{LINE_BREAK}|\n)", text):
        if part == "\t":
            pieces.append(etree.Element(_w("tab")))
        elif part in (LINE_BREAK, "\n"):
            pieces.append(etree.Element(_w("br")))
        elif part:
            node = etree.Element(_w("t"))
            node.text = part
            node.set(XML_SPACE, "preserve")
            pieces.append(node)
    return pieces


def _replace_text(paragraph: etree._Element, text: str) -> None:
    runs = [node for node in _own_nodes(paragraph) if node.tag == _w("r")]
    text_runs = [run for run in runs if any(child.tag in _TEXT_PIECES for child in run)]
    for run in text_runs:
        for child in [child for child in run if child.tag in _TEXT_PIECES]:
            run.remove(child)
    if text_runs:
        first = text_runs[0]
        for later in text_runs[1:]:
            if all(child.tag == _w("rPr") for child in later):
                later.getparent().remove(later)
    else:
        # An empty paragraph: write with the formatting Word keeps for its paragraph mark.
        first = etree.SubElement(paragraph, _w("r"))
        mark = paragraph.find(f"{_w('pPr')}/{_w('rPr')}")
        if mark is not None:
            first.append(etree.fromstring(etree.tostring(mark)))
    for piece in _new_pieces(text):
        first.append(piece)


LATIN_FONT = "Times New Roman"


def _child(parent: etree._Element, tag: str, before: tuple[str, ...] = ()) -> etree._Element:
    """The child with this tag, created in a place the schema accepts if it is missing."""
    found = parent.find(_w(tag))
    if found is not None:
        return found
    node = etree.Element(_w(tag))
    for position, child in enumerate(parent):
        if child.tag in {_w(name) for name in before}:
            parent.insert(position, node)
            return node
    if tag in ("rPr", "pPr"):
        parent.insert(0, node)
    else:
        parent.append(node)
    return node


def _latin_font(properties: etree._Element, font: str) -> None:
    """Set the font Word uses for Latin letters. Chinese text keeps its own East Asian font."""
    fonts = properties.find(_w("rFonts"))
    if fonts is None:
        fonts = etree.Element(_w("rFonts"))
        style = properties.find(_w("rStyle"))
        properties.insert(1 if style is not None and properties.index(style) == 0 else 0, fonts)
    for name in ("asciiTheme", "hAnsiTheme"):
        fonts.attrib.pop(_w(name), None)  # a theme font would win over the named one
    fonts.set(_w("ascii"), font)
    fonts.set(_w("hAnsi"), font)


def _paragraph_font(paragraph: etree._Element, font: str) -> None:
    for node in _own_nodes(paragraph):
        if node.tag == _w("r"):
            _latin_font(_child(node, "rPr"), font)
    properties = _child(paragraph, "pPr")
    _latin_font(_child(properties, "rPr", before=("sectPr", "pPrChange")), font)


def _font_everywhere(package: Package, root: etree._Element, font: str) -> None:
    for run in root.iter(_w("r")):
        _latin_font(_child(run, "rPr"), font)
    styles = package.main_part().related("/styles")
    if styles is None:
        return
    tree = styles.xml()
    defaults = _child(_child(_child(tree, "docDefaults"), "rPrDefault"), "rPr")
    _latin_font(defaults, font)
    for fonts in tree.iter(_w("rFonts")):
        if any(fonts.get(_w(name)) is not None for name in ("ascii", "hAnsi", "asciiTheme", "hAnsiTheme")):
            _latin_font(fonts.getparent(), font)
    styles.set_xml(tree)


def write_word(source: str | Path, edits: dict[int, str], out: str | Path, *,
               font_paragraphs: tuple[int, ...] = (), font_everywhere: bool = False) -> None:
    """Write a copy of `source` with paragraph text replaced, and Times New Roman where asked."""
    package, part, root = _document_xml(source)
    try:
        paragraphs = list(_paragraphs(root.find(_w("body"))))
        for index, text in edits.items():
            if not 0 <= index < len(paragraphs):
                raise CorruptFile(f"Paragraph {index + 1} no longer exists in {Path(source).name}.")
            if _text_of(paragraphs[index])[1]:
                raise CorruptFile("Paragraphs holding fields, such as page numbers or a table of contents, cannot be edited.")
            _replace_text(paragraphs[index], text)
        for index in font_paragraphs:
            if 0 <= index < len(paragraphs):
                _paragraph_font(paragraphs[index], LATIN_FONT)
        if font_everywhere:
            _font_everywhere(package, root, LATIN_FONT)
        part.set_xml(root)
        package.save(out)
    finally:
        package.close()


def apply_text_edits(source: str | Path, edits: dict[int, str], out: str | Path) -> None:
    write_word(source, edits, out)


def read_restricted(path: str | Path) -> bool:
    """True when the author used Word's Restrict Editing, which James Viewer respects."""
    try:
        package = Package(path)
    except Exception:
        return False
    try:
        settings = package.main_part().related("/settings")
        if settings is None:
            return False
        protection = settings.xml().find(_w("documentProtection"))
        if protection is None:
            return False
        enforced = (protection.get(_w("enforcement")) or "").lower() in ("1", "true", "on")
        return enforced and (protection.get(_w("edit")) or "none") != "none"
    finally:
        package.close()
