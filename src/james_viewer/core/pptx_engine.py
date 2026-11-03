"""Slide-level operations on PPTX packages.

A deck is described by an ordered list of SlideSpec values. `build_deck` turns
that description into a new package: slides, layouts, masters, themes, media,
charts and notes are carried across as parts, so anything not edited stays as
it was in the source file.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .errors import CorruptFile, ViewerError
from .opc import Package, Part, Rel

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "p188": "http://schemas.microsoft.com/office/powerpoint/2018/8/main",
}
P = "{%s}" % NS["p"]
A = "{%s}" % NS["a"]
R = "{%s}" % NS["r"]
P14 = "{%s}" % NS["p14"]
MC = "{%s}" % NS["mc"]

REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_SLIDE = REL_BASE + "/slide"
REL_LAYOUT = REL_BASE + "/slideLayout"
REL_MASTER = REL_BASE + "/slideMaster"
REL_THEME = REL_BASE + "/theme"
REL_NOTES_MASTER = REL_BASE + "/notesMaster"
CT_SLIDE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
CT_PRES = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
SECTION_EXT = "{521415D9-36F7-43E2-AB2F-B90AF26B5E84}"

LINE_BREAK = " "
_SHARED_SUFFIXES = ("/image", "/audio", "/video", "/media", "/hdphoto", "/wdp")
_COMMENT_SUFFIXES = ("/comments",)


@dataclass(frozen=True)
class SlideSpec:
    source: str  # path of the PPTX holding this slide
    index: int  # slide position in that file, or -1 for a new slide
    layout: str | None = None  # layout part name, for new slides
    edits: tuple[tuple[int, str], ...] = ()  # (text body index, new text)


@dataclass
class SlideInfo:
    index: int
    hidden: bool
    title: str
    has_slide_number: bool
    layout_name: str
    layout_part: str | None


@dataclass
class DeckInfo:
    width_emu: int
    height_emu: int
    slides: list[SlideInfo]
    layouts: list[tuple[str, str]]  # (part name, display name)
    modify_protected: bool


@dataclass
class TextBlock:
    index: int
    name: str
    text: str


@dataclass
class BuildReport:
    warnings: list[str] = field(default_factory=list)


def _presentation_slides(package: Package) -> tuple[Part, etree._Element, list[tuple[etree._Element, Part]]]:
    pres = package.main_part()
    root = pres.xml()
    slides = []
    id_list = root.find(P + "sldIdLst")
    if id_list is not None:
        for node in id_list:
            rel = pres.rels.get(node.get(R + "id"))
            part = pres.target_part(rel) if rel else None
            if part is not None:
                slides.append((node, part))
    return pres, root, slides


def _slide_size(root: etree._Element) -> tuple[int, int]:
    size = root.find(P + "sldSz")
    if size is None:
        return 9144000, 6858000
    return int(size.get("cx")), int(size.get("cy"))


def _shape_text(body: etree._Element) -> str:
    lines = []
    for paragraph in body.findall(A + "p"):
        text = []
        for child in paragraph:
            if child.tag == A + "br":
                text.append(LINE_BREAK)
            elif child.tag in (A + "r", A + "fld"):
                node = child.find(A + "t")
                text.append(node.text or "" if node is not None else "")
        lines.append("".join(text))
    return "\n".join(lines)


def read_deck_info(path: str | Path) -> DeckInfo:
    with Package(path) as package:
        pres, root, slides = _presentation_slides(package)
        width, height = _slide_size(root)
        layouts = []
        seen = set()
        for master_rel in pres.rels_of_type("/slideMaster"):
            master = pres.target_part(master_rel)
            if master is None:
                continue
            for layout_rel in master.rels_of_type("/slideLayout"):
                layout = master.target_part(layout_rel)
                if layout is None or layout.name in seen:
                    continue
                seen.add(layout.name)
                layouts.append((layout.name, _layout_name(layout)))
        infos = []
        for index, (_node, part) in enumerate(slides):
            xml = part.xml()
            layout = part.related("/slideLayout")
            infos.append(
                SlideInfo(
                    index=index,
                    hidden=xml.get("show") == "0",
                    title=_slide_title(xml),
                    has_slide_number=b'type="slidenum"' in part.blob,
                    layout_name=_layout_name(layout) if layout else "",
                    layout_part=layout.name if layout else None,
                )
            )
        return DeckInfo(width, height, infos, layouts, root.find(P + "modifyVerifier") is not None)


def _layout_name(layout: Part) -> str:
    node = layout.xml().find(P + "cSld")
    return (node.get("name") if node is not None else "") or posixpath.basename(layout.name)


def _slide_title(root: etree._Element) -> str:
    for shape in root.iter(P + "sp"):
        placeholder = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
        if placeholder is not None and placeholder.get("type") in ("title", "ctrTitle"):
            body = shape.find(P + "txBody")
            if body is not None:
                return _shape_text(body).replace("\n", " ").replace(LINE_BREAK, " ").strip()
    return ""


# --- text ---------------------------------------------------------------------------------------


def _text_bodies(root: etree._Element) -> list[etree._Element]:
    return [
        node
        for node in root.iter()
        if isinstance(node.tag, str) and etree.QName(node).localname == "txBody"
    ]


def _is_fallback(node: etree._Element) -> bool:
    return any(parent.tag == MC + "Fallback" for parent in node.iterancestors())


def _body_label(body: etree._Element, counts: dict[str, int]) -> str:
    cell = body.getparent()
    if cell.tag == A + "tc":
        row = cell.getparent()
        table = row.getparent()
        rows = table.findall(A + "tr")
        return f"Table cell {rows.index(row) + 1}.{row.findall(A + 'tc').index(cell) + 1}"
    name = cell.find(f"{P}nvSpPr/{P}cNvPr")
    label = (name.get("name") if name is not None else "") or "Text"
    counts[label] = counts.get(label, 0) + 1
    return label


def list_text_blocks(path: str | Path, spec: SlideSpec) -> list[TextBlock]:
    """Editable text on one slide, with `spec.edits` already applied."""
    with Package(path) as package:
        if spec.index < 0:
            if not spec.layout or spec.layout not in package.parts:
                return []
            root = etree.fromstring(_new_slide_xml(package.parts[spec.layout]))
        else:
            _pres, _root, slides = _presentation_slides(package)
            root = slides[spec.index][1].xml()
    _apply_edits_to_root(root, spec.edits)
    blocks = []
    counts: dict[str, int] = {}
    for index, body in enumerate(_text_bodies(root)):
        if _is_fallback(body):
            continue
        blocks.append(TextBlock(index, _body_label(body, counts), _shape_text(body)))
    return blocks


def _apply_edits_to_root(root: etree._Element, edits: tuple[tuple[int, str], ...]) -> None:
    if not edits:
        return
    bodies = _text_bodies(root)
    for index, text in edits:
        if 0 <= index < len(bodies):
            _set_body_text(bodies[index], text)


def _set_body_text(body: etree._Element, text: str) -> None:
    paragraphs = body.findall(A + "p")
    old_lines = [_paragraph_text(p) for p in paragraphs]
    new_lines = text.split("\n")
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    template = paragraphs[-1] if paragraphs else None
    anchor = None  # the paragraph most recently placed, new ones go right after it
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            anchor = paragraphs[a1 - 1]
            continue
        paired = min(a1 - a0, b1 - b0) if op == "replace" else 0
        for offset in range(paired):
            _set_paragraph_text(paragraphs[a0 + offset], new_lines[b0 + offset])
            anchor = paragraphs[a0 + offset]
        for line in new_lines[b0 + paired : b1]:
            model = anchor if anchor is not None else template
            fresh = _blank_paragraph_like(model)
            if anchor is not None:
                anchor.addnext(fresh)
            elif paragraphs and paragraphs[0].getparent() is body:
                paragraphs[0].addprevious(fresh)
            else:
                body.append(fresh)
            _set_paragraph_text(fresh, line)
            anchor = fresh
        for paragraph in paragraphs[a0 + paired : a1]:
            body.remove(paragraph)
    if body.find(A + "p") is None:
        body.append(etree.SubElement(body, A + "p"))


def _paragraph_text(paragraph: etree._Element) -> str:
    text = []
    for child in paragraph:
        if child.tag == A + "br":
            text.append(LINE_BREAK)
        elif child.tag in (A + "r", A + "fld"):
            node = child.find(A + "t")
            text.append(node.text or "" if node is not None else "")
    return "".join(text)


def _blank_paragraph_like(model: etree._Element | None) -> etree._Element:
    paragraph = etree.Element(A + "p")
    if model is None:
        return paragraph
    properties = model.find(A + "pPr")
    if properties is not None:
        paragraph.append(copy.deepcopy(properties))
    first_run = model.find(A + "r")
    run_props = first_run.find(A + "rPr") if first_run is not None else model.find(A + "endParaRPr")
    if run_props is not None:
        end = copy.deepcopy(run_props)
        end.tag = A + "endParaRPr"
        paragraph.append(end)
    return paragraph


def _set_paragraph_text(paragraph: etree._Element, new_text: str) -> None:
    """Replace the text, keeping each untouched character's run formatting."""
    tokens = [c for c in paragraph if c.tag in (A + "r", A + "br", A + "fld")]
    old_chars: list[str] = []
    owners: list[etree._Element] = []
    for token in tokens:
        if token.tag == A + "br":
            old_chars.append(LINE_BREAK)
            owners.append(token)
        else:
            node = token.find(A + "t")
            value = (node.text or "") if node is not None else ""
            old_chars.extend(value)
            owners.extend([token] * len(value))
    old_text = "".join(old_chars)
    if old_text == new_text:
        return

    prefix = 0
    limit = min(len(old_text), len(new_text))
    while prefix < limit and old_text[prefix] == new_text[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old_text[-1 - suffix] == new_text[-1 - suffix]:
        suffix += 1

    middle = new_text[prefix : len(new_text) - suffix]
    if prefix > 0 and owners[prefix - 1].tag != A + "br":
        insert_owner = owners[prefix - 1]
    elif prefix < len(owners) and owners[prefix].tag != A + "br":
        insert_owner = owners[prefix]
    else:
        insert_owner = next((t for t in tokens if t.tag != A + "br"), None)

    new_owners = (
        owners[:prefix]
        + [insert_owner] * len(middle)
        + (owners[len(owners) - suffix :] if suffix else [])
    )

    position = list(paragraph).index(tokens[0]) if tokens else _text_insert_position(paragraph)
    for token in tokens:
        paragraph.remove(token)

    end_props = paragraph.find(A + "endParaRPr")
    built: list[etree._Element] = []
    index = 0
    while index < len(new_text):
        character = new_text[index]
        owner = new_owners[index]
        if character == LINE_BREAK:
            element = (
                copy.deepcopy(owner) if owner is not None and owner.tag == A + "br" else etree.Element(A + "br")
            )
            built.append(element)
            index += 1
            continue
        stop = index
        while stop < len(new_text) and new_text[stop] != LINE_BREAK and new_owners[stop] is owner:
            stop += 1
        if owner is not None and owner.tag != A + "br":
            element = copy.deepcopy(owner)
            t_node = element.find(A + "t")
            if t_node is None:
                t_node = etree.SubElement(element, A + "t")
        else:
            element = etree.Element(A + "r")
            if end_props is not None:
                run_props = copy.deepcopy(end_props)
                run_props.tag = A + "rPr"
                element.append(run_props)
            t_node = etree.SubElement(element, A + "t")
        props = element.find(A + "rPr")
        if props is not None:
            props.attrib.pop("err", None)
            props.attrib.pop("dirty", None)
        t_node.text = new_text[index:stop]
        built.append(element)
        index = stop

    for offset, element in enumerate(built):
        paragraph.insert(position + offset, element)


def _text_insert_position(paragraph: etree._Element) -> int:
    properties = paragraph.find(A + "pPr")
    return 0 if properties is None else list(paragraph).index(properties) + 1


# --- new slides ---------------------------------------------------------------------------------


def _new_slide_xml(layout: Part) -> bytes:
    layout_root = layout.xml()
    nsmap = {"a": NS["a"], "r": NS["r"], "p": NS["p"]}
    root = etree.Element(P + "sld", nsmap=nsmap)
    common = etree.SubElement(root, P + "cSld")
    tree = etree.SubElement(common, P + "spTree")
    group = etree.SubElement(tree, P + "nvGrpSpPr")
    etree.SubElement(group, P + "cNvPr", id="1", name="")
    etree.SubElement(group, P + "cNvGrpSpPr")
    etree.SubElement(group, P + "nvPr")
    etree.SubElement(tree, P + "grpSpPr")
    shape_id = 2
    for source in layout_root.iter(P + "sp"):
        placeholder = source.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
        if placeholder is None or placeholder.get("type") in ("dt", "ftr", "sldNum"):
            continue
        shape = etree.SubElement(tree, P + "sp")
        non_visual = etree.SubElement(shape, P + "nvSpPr")
        source_name = source.find(f"{P}nvSpPr/{P}cNvPr")
        name = source_name.get("name") if source_name is not None else f"Placeholder {shape_id}"
        etree.SubElement(non_visual, P + "cNvPr", id=str(shape_id), name=name)
        locks = etree.SubElement(non_visual, P + "cNvSpPr")
        etree.SubElement(locks, A + "spLocks", noGrp="1")
        props = etree.SubElement(non_visual, P + "nvPr")
        props.append(copy.deepcopy(placeholder))
        etree.SubElement(shape, P + "spPr")
        if placeholder.get("type") != "pic":
            body = etree.SubElement(shape, P + "txBody")
            etree.SubElement(body, A + "bodyPr")
            etree.SubElement(body, A + "lstStyle")
            paragraph = etree.SubElement(body, A + "p")
            etree.SubElement(paragraph, A + "endParaRPr", lang="en-US")
        shape_id += 1
    overrides = etree.SubElement(root, P + "clrMapOvr")
    etree.SubElement(overrides, A + "masterClrMapping")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


# --- geometry scaling for slides that move into a deck of a different size ----------------------


def _scale_tree(root: etree._Element, scale: float, dx: int, dy: int) -> None:
    def scaled(value: str) -> str:
        return str(int(round(int(value) * scale)))

    tree = root.find(f"{P}cSld/{P}spTree")
    if tree is not None:
        for shape in tree:
            transform = None
            for path in (f"{P}spPr/{A}xfrm", f"{P}grpSpPr/{A}xfrm", f"{P}xfrm"):
                transform = shape.find(path)
                if transform is not None:
                    break
            if transform is None:
                continue
            offset, extent = transform.find(A + "off"), transform.find(A + "ext")
            if offset is not None:
                offset.set("x", str(int(round(int(offset.get("x")) * scale)) + dx))
                offset.set("y", str(int(round(int(offset.get("y")) * scale)) + dy))
            if extent is not None:
                extent.set("cx", scaled(extent.get("cx")))
                extent.set("cy", scaled(extent.get("cy")))
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if node.tag in (A + "rPr", A + "defRPr", A + "endParaRPr") and node.get("sz"):
            node.set("sz", str(max(100, int(round(int(node.get("sz")) * scale)))))
        elif node.tag == A + "gridCol" and node.get("w"):
            node.set("w", scaled(node.get("w")))
        elif node.tag == A + "tr" and node.get("h"):
            node.set("h", scaled(node.get("h")))


# --- building -----------------------------------------------------------------------------------


class _Builder:
    def __init__(self, base_path: str, report: BuildReport):
        self.report = report
        self.base_path = str(base_path)
        self.packages: dict[str, Package] = {self.base_path: Package(base_path)}
        self.target = self.packages[self.base_path]
        self.pres, self.root, self.base_slides = _presentation_slides(self.target)
        self.size = _slide_size(self.root)
        self.part_map: dict[tuple[str, str], Part] = {}
        self.layout_map: dict[tuple[str, str], Part] = {}
        self.master_map: dict[tuple[str, str], Part] = {}
        self.imported_masters: dict[str, set[str]] = {}
        self.media_by_hash: dict[str, Part] = {}
        self.pending_slide_links: list[tuple[Part, str, tuple[str, str]]] = []
        self.slide_map: dict[tuple[str, str], Part] = {}
        self.used_base: set[int] = set()
        self.notes_master: Part | None = self.pres.related("/notesMaster")
        self.fingerprints: dict[str, dict[str, Part]] = {}
        self.scale_warned = False

    def close(self) -> None:
        for package in self.packages.values():
            package.close()

    def package_for(self, path: str) -> Package:
        path = str(path)
        if path not in self.packages:
            self.packages[path] = Package(path)
        return self.packages[path]

    # identifiers

    def _new_slide_id(self, taken: set[int]) -> str:
        value = max(taken | {255}) + 1
        taken.add(value)
        return str(value)

    def _master_layout_ids(self) -> list[int]:
        ids = []
        master_list = self.root.find(P + "sldMasterIdLst")
        if master_list is not None:
            for node in master_list:
                ids.append(int(node.get("id")))
                rel = self.pres.rels.get(node.get(R + "id"))
                master = self.pres.target_part(rel) if rel else None
                if master is not None:
                    layouts = master.xml().find(P + "sldLayoutIdLst")
                    if layouts is not None:
                        ids.extend(int(n.get("id")) for n in layouts)
        return ids

    # part copying

    def _copy_media(self, source: Part) -> Part:
        key = (str(source.package.path), source.name)
        if key in self.part_map:
            return self.part_map[key]
        digest = hashlib.sha1(source.blob).hexdigest()
        existing = self.media_by_hash.get(digest)
        if existing is None:
            for part in self.target.parts.values():
                if part.rels or part.content_type != source.content_type:
                    continue
                if len(part.blob) == len(source.blob) and part.blob == source.blob:
                    existing = part
                    break
        if existing is None:
            existing = self.target.add_part(
                Part(self.target, self.target.next_partname(source.name), source.content_type, source.blob)
            )
        self.media_by_hash[digest] = existing
        self.part_map[key] = existing
        return existing

    def _copy_tree(self, source: Part, *, same_package: bool, mapping: dict[str, Part]) -> Part:
        """Deep-copy `source` and what it owns. Shared parts are linked, not duplicated."""
        if source.name in mapping:
            return mapping[source.name]
        new = Part(self.target, self.target.next_partname(source.name), source.content_type, source.blob)
        self.target.add_part(new)
        mapping[source.name] = new
        for rel in source.rels.values():
            copied = self._copy_rel(source, new, rel, same_package=same_package, mapping=mapping)
            if copied is not None:
                new.rels[rel.id] = copied
        return new

    def _copy_rel(self, source: Part, new: Part, rel: Rel, *, same_package: bool, mapping: dict[str, Part]) -> Rel | None:
        if rel.external:
            return Rel(rel.id, rel.type, rel.target, True)
        target = source.target_part(rel)
        if target is None:
            return Rel(rel.id, rel.type, rel.target, False)
        kind = rel.type
        if target.name in mapping:
            resolved = mapping[target.name]
        elif kind.endswith("/slideLayout"):
            resolved = target if same_package else self._import_layout(target)
        elif kind.endswith(_SHARED_SUFFIXES):
            resolved = target if same_package else self._copy_media(target)
        elif kind.endswith("/notesMaster"):
            resolved = target if same_package else self._notes_master_for(target)
        elif kind.endswith("/slideMaster"):
            resolved = target if same_package else self._import_master(target)
        elif kind.endswith("/slide"):
            if same_package:
                resolved = target
            else:
                self.pending_slide_links.append((new, rel.id, (str(target.package.path), target.name)))
                return None
        elif kind.endswith(_COMMENT_SUFFIXES):
            return None
        else:
            resolved = self._copy_tree(target, same_package=same_package, mapping=mapping)
        return Rel(rel.id, rel.type, resolved.name, False)

    # masters and layouts

    def _fingerprint(self, part: Part) -> str:
        digest = hashlib.sha1(part.blob)
        for rel in sorted(part.rels.values(), key=lambda r: r.id):
            if rel.external or rel.type.endswith(("/slideLayout", "/slideMaster")):
                continue
            target = part.target_part(rel)
            if target is not None:
                digest.update(hashlib.sha1(target.blob).digest())
        return digest.hexdigest()

    def _find_matching_master(self, master: Part) -> Part | None:
        if self._scale_for(master.package) is not None:
            return None  # identical XML, but it must be rescaled for this deck
        wanted = self._fingerprint(master)
        for rel in self.pres.rels_of_type("/slideMaster"):
            candidate = self.pres.target_part(rel)
            if candidate is not None and self._fingerprint(candidate) == wanted:
                return candidate
        return None

    def _import_master(self, master: Part) -> Part:
        key = (str(master.package.path), master.name)
        if key in self.master_map:
            return self.master_map[key]
        matching = self._find_matching_master(master)
        if matching is not None:
            self.master_map[key] = matching
            return matching

        new_master = Part(self.target, self.target.next_partname(master.name), master.content_type, master.blob)
        self.target.add_part(new_master)
        self.master_map[key] = new_master
        for rel in master.rels.values():
            if rel.external:
                new_master.rels[rel.id] = Rel(rel.id, rel.type, rel.target, True)
                continue
            target = master.target_part(rel)
            if target is None:
                new_master.rels[rel.id] = Rel(rel.id, rel.type, rel.target, False)
            elif rel.type.endswith("/slideLayout"):
                continue
            elif rel.type.endswith(_SHARED_SUFFIXES):
                new_master.rels[rel.id] = Rel(rel.id, rel.type, self._copy_media(target).name, False)
            else:
                copied = self._copy_tree(target, same_package=False, mapping={})
                new_master.rels[rel.id] = Rel(rel.id, rel.type, copied.name, False)

        xml = new_master.xml()
        layouts = xml.find(P + "sldLayoutIdLst")
        if layouts is not None:
            for node in list(layouts):
                layouts.remove(node)
        self._scale_part_if_needed(new_master, xml, master.package)
        new_master.set_xml(xml)

        master_list = self.root.find(P + "sldMasterIdLst")
        new_id = max(self._master_layout_ids() + [2147483647]) + 1
        rid = self.pres.relate_to(new_master, REL_MASTER)
        etree.SubElement(master_list, P + "sldMasterId", id=str(new_id)).set(R + "id", rid)
        return new_master

    def _import_layout(self, layout: Part) -> Part:
        key = (str(layout.package.path), layout.name)
        if key in self.layout_map:
            return self.layout_map[key]
        source_master = layout.related("/slideMaster")
        if source_master is None:
            raise CorruptFile(f"The layout {layout.name} has no slide master.")
        master = self._import_master(source_master)

        if master.package is self.target and self._was_matched(source_master, master):
            wanted = _layout_name(layout)
            blob_hash = hashlib.sha1(layout.blob).digest()
            for rel in master.rels_of_type("/slideLayout"):
                candidate = master.target_part(rel)
                if candidate is not None and (
                    hashlib.sha1(candidate.blob).digest() == blob_hash or _layout_name(candidate) == wanted
                ):
                    self.layout_map[key] = candidate
                    return candidate

        new_layout = Part(self.target, self.target.next_partname(layout.name), layout.content_type, layout.blob)
        self.target.add_part(new_layout)
        self.layout_map[key] = new_layout
        mapping = {layout.name: new_layout, source_master.name: master}
        for rel in layout.rels.values():
            copied = self._copy_rel(layout, new_layout, rel, same_package=False, mapping=mapping)
            if copied is not None:
                new_layout.rels[rel.id] = copied
        xml = new_layout.xml()
        self._scale_part_if_needed(new_layout, xml, layout.package)
        new_layout.set_xml(xml)

        master_xml = master.xml()
        layout_list = master_xml.find(P + "sldLayoutIdLst")
        if layout_list is None:
            layout_list = etree.Element(P + "sldLayoutIdLst")
            master_xml.find(P + "txStyles").addprevious(layout_list)
        new_id = max(self._master_layout_ids() + [2147483647]) + 1
        rid = master.relate_to(new_layout, REL_LAYOUT)
        etree.SubElement(layout_list, P + "sldLayoutId", id=str(new_id)).set(R + "id", rid)
        master.set_xml(master_xml)
        return new_layout

    def _was_matched(self, source_master: Part, master: Part) -> bool:
        return self._fingerprint(source_master) == self._fingerprint(master)

    def _notes_master_for(self, source: Part) -> Part:
        if self.notes_master is not None:
            return self.notes_master
        new = Part(self.target, self.target.next_partname(source.name), source.content_type, source.blob)
        self.target.add_part(new)
        self.notes_master = new
        for rel in source.rels.values():
            if rel.external:
                new.rels[rel.id] = Rel(rel.id, rel.type, rel.target, True)
                continue
            target = source.target_part(rel)
            if target is None:
                new.rels[rel.id] = Rel(rel.id, rel.type, rel.target, False)
            elif rel.type.endswith(_SHARED_SUFFIXES):
                new.rels[rel.id] = Rel(rel.id, rel.type, self._copy_media(target).name, False)
            else:
                new.rels[rel.id] = Rel(rel.id, rel.type, self._copy_tree(target, same_package=False, mapping={}).name, False)
        rid = self.pres.relate_to(new, REL_NOTES_MASTER)
        notes_list = self.root.find(P + "notesMasterIdLst")
        if notes_list is None:
            notes_list = etree.Element(P + "notesMasterIdLst")
            self.root.find(P + "sldMasterIdLst").addnext(notes_list)
        etree.SubElement(notes_list, P + "notesMasterId").set(R + "id", rid)
        return new

    # scaling

    def _scale_for(self, package: Package) -> tuple[float, int, int] | None:
        _pres, root, _slides = _presentation_slides(package)
        width, height = _slide_size(root)
        if (width, height) == self.size:
            return None
        scale = min(self.size[0] / width, self.size[1] / height)
        return scale, int((self.size[0] - width * scale) / 2), int((self.size[1] - height * scale) / 2)

    def _scale_part_if_needed(self, part: Part, xml: etree._Element, package: Package) -> None:
        factors = self._scale_for(package)
        if factors is None:
            return
        _scale_tree(xml, *factors)
        if not self.scale_warned:
            self.scale_warned = True
            self.report.warnings.append(
                "Some slides came from a deck with a different slide size. They were scaled to fit "
                "without distortion, so margins may appear around them."
            )

    # slides

    def slide_part_for(self, spec: SlideSpec) -> tuple[Part, str | None]:
        """Return the part for a spec, and the existing sldId value when reused."""
        same_package = str(spec.source) == self.base_path
        if spec.index < 0:
            package = self.package_for(spec.source)
            layout = package.parts.get(spec.layout or "")
            if layout is None:
                raise ViewerError("The layout for a new slide is no longer available.")
            resolved = layout if same_package else self._import_layout(layout)
            name = self.target.next_partname("/ppt/slides/slide1.xml")
            part = Part(self.target, name, CT_SLIDE, _new_slide_xml(layout))
            self.target.add_part(part)
            part.relate_to(resolved, REL_LAYOUT)
            return part, None

        if same_package:
            node, part = self.base_slides[spec.index]
            if spec.index not in self.used_base:
                self.used_base.add(spec.index)
                return part, node.get("id")
            clone = self._copy_tree(part, same_package=True, mapping={})
            self._strip_comment_refs(clone)
            return clone, None

        package = self.package_for(spec.source)
        _pres, _root, slides = _presentation_slides(package)
        source_part = slides[spec.index][1]
        imported = self._copy_tree(source_part, same_package=False, mapping={})
        self.slide_map.setdefault((str(package.path), source_part.name), imported)
        self._strip_comment_refs(imported)
        xml = imported.xml()
        self._scale_part_if_needed(imported, xml, package)
        imported.set_xml(xml)
        return imported, None

    @staticmethod
    def _strip_comment_refs(part: Part) -> None:
        """Comments are not carried over; remove what pointed at them."""
        doomed = part.rels_of_type("/comments")
        for rel in doomed:
            del part.rels[rel.id]
        if b"commentRel" not in part.blob:
            return
        xml = part.xml()
        for ext in xml.xpath("//*[local-name()='commentRel']"):
            holder = ext.getparent()
            holder.getparent().remove(holder)
        part.set_xml(xml)

    def finish(self, ordered: list[tuple[Part, str | None]], specs: list[SlideSpec]) -> None:
        id_list = self.root.find(P + "sldIdLst")
        if id_list is None:
            id_list = etree.Element(P + "sldIdLst")
            anchor = self.root.find(P + "notesMasterIdLst")
            if anchor is None:
                anchor = self.root.find(P + "sldMasterIdLst")
            anchor.addnext(id_list)

        old_ids = {node.get("id"): node.get(R + "id") for node in id_list}
        old_order = [node.get("id") for node in id_list]
        taken = {int(value) for value in old_ids}
        kept_parts = set()
        new_nodes = []
        new_ids: list[str] = []
        origin: dict[str, str | None] = {}
        for (part, reuse_id), spec in zip(ordered, specs):
            kept_parts.add(part.name)
            if reuse_id is not None:
                slide_id, rid = reuse_id, old_ids[reuse_id]
            else:
                slide_id = self._new_slide_id(taken)
                rid = self.pres.relate_to(part, REL_SLIDE)
                same = str(spec.source) == self.base_path and spec.index >= 0
                origin[slide_id] = self.base_slides[spec.index][0].get("id") if same else None
            node = etree.Element(P + "sldId", id=slide_id)
            node.set(R + "id", rid)
            new_nodes.append(node)
            new_ids.append(slide_id)

        for node in list(id_list):
            id_list.remove(node)
        id_list.extend(new_nodes)

        kept_rids = {node.get(R + "id") for node in new_nodes}
        removed_rids = [
            rid
            for rid, rel in list(self.pres.rels.items())
            if rel.type.endswith("/slide") and rid not in kept_rids
        ]
        for rid in removed_rids:
            del self.pres.rels[rid]
        self._prune_custom_shows(set(removed_rids))

        for part_owner, rid, key in self.pending_slide_links:
            target = self.slide_map.get(key)
            if target is not None:
                part_owner.rels[rid] = Rel(rid, REL_SLIDE, target.name, False)
        for part, _reuse in ordered:
            self._strip_dead_links(part, kept_parts)

        self._rebuild_sections(new_ids, old_order, origin)
        self._update_app_properties(len(new_nodes))
        self._drop_stale_thumbnail()

    def _prune_custom_shows(self, removed: set[str]) -> None:
        shows = self.root.find(P + "custShowLst")
        if shows is None:
            return
        for show in list(shows):
            slides = show.find(P + "sldLst")
            if slides is None:
                continue
            for node in list(slides):
                if node.get(R + "id") in removed:
                    slides.remove(node)
            if len(slides) == 0:
                shows.remove(show)
        if len(shows) == 0:
            self.root.remove(shows)

    @staticmethod
    def _strip_dead_links(part: Part, kept: set[str]) -> None:
        dead = {}
        names = {"/slide": True}
        for rid, rel in part.rels.items():
            if rel.external or not rel.type.endswith("/slide"):
                continue
            target = part.target_part(rel)
            if target is not None and target.name not in kept:
                dead[rid] = rel
        known = {r.id for r in part.rels.values()}
        xml = None
        if dead or b"hlinksldjump" in part.blob:
            xml = part.xml()
            for node in list(xml.iter(A + "hlinkClick", A + "hlinkHover")):
                rid = node.get(R + "id")
                if rid in dead or (rid and rid not in known):
                    node.getparent().remove(node)
            part.set_xml(xml)
        for rid in dead:
            del part.rels[rid]
        del names

    def _rebuild_sections(self, new_ids: list[str], old_order: list[str], origin: dict[str, str | None]) -> None:
        holder = None
        ext_list = self.root.find(P + "extLst")
        if ext_list is None:
            return
        for ext in ext_list:
            if ext.get("uri") == SECTION_EXT:
                holder = ext.find(P14 + "sectionLst")
                break
        if holder is None:
            return
        sections = list(holder)
        section_of: dict[str, etree._Element] = {}
        for section in sections:
            slides = section.find(P14 + "sldIdLst")
            if slides is None:
                continue
            for node in slides:
                section_of[node.get("id")] = section

        runs: list[tuple[etree._Element, list[str]]] = []
        seen = set()
        for slide_id in new_ids:
            section = section_of.get(slide_id)
            if section is None and origin.get(slide_id):
                section = section_of.get(origin[slide_id])
            if section is None or (runs and id(section) in seen and runs[-1][0] is not section):
                section = runs[-1][0] if runs else (sections[0] if sections else None)
            if section is None:
                continue
            if not runs or runs[-1][0] is not section:
                runs.append((section, []))
                seen.add(id(section))
            runs[-1][1].append(slide_id)

        for section in sections:
            holder.remove(section)
        for section, ids in runs:
            slides = section.find(P14 + "sldIdLst")
            if slides is None:
                slides = etree.SubElement(section, P14 + "sldIdLst")
            for node in list(slides):
                slides.remove(node)
            for slide_id in ids:
                etree.SubElement(slides, P14 + "sldId", id=slide_id)
            holder.append(section)

    def _update_app_properties(self, count: int) -> None:
        for rel in self.target.root_rels.values():
            if not rel.type.endswith("/extended-properties"):
                continue
            part = self.target.resolve("/", rel.target)
            if part is None:
                return
            try:
                xml = part.xml()
            except etree.XMLSyntaxError:
                return
            for node in xml:
                if isinstance(node.tag, str) and etree.QName(node).localname == "Slides":
                    node.text = str(count)
                    part.set_xml(xml)
                    return

    def _drop_stale_thumbnail(self) -> None:
        for rid, rel in list(self.target.root_rels.items()):
            if rel.type.endswith("/thumbnail"):
                del self.target.root_rels[rid]


def _widen_unwrapped_text(root: etree._Element) -> bool:
    """Give no-wrap text room, because the office engine can still wrap it inside its box.

    Widening an invisible box around its text anchor does not move the text, so this
    only removes the false line break. It runs on temporary render decks only.
    """
    changed = False
    for shape in root.iter(P + "sp"):
        body = shape.find(P + "txBody")
        props = shape.find(P + "spPr")
        if body is None or props is None or shape.find(P + "style") is not None:
            continue
        body_props = body.find(A + "bodyPr")
        transform = props.find(A + "xfrm")
        if body_props is None or body_props.get("wrap") != "none" or transform is None or transform.get("rot"):
            continue
        if any(props.find(A + tag) is not None for tag in ("solidFill", "gradFill", "blipFill", "pattFill")):
            continue
        line = props.find(A + "ln")
        if line is not None and line.find(A + "noFill") is None:
            continue
        offset, extent = transform.find(A + "off"), transform.find(A + "ext")
        if offset is None or extent is None:
            continue
        width = int(extent.get("cx"))
        extra = width // 2
        paragraph_props = body.find(f"{A}p/{A}pPr")
        align = paragraph_props.get("algn") if paragraph_props is not None else None
        shift = {"ctr": extra // 2, "r": extra}.get(align, 0)
        offset.set("x", str(int(offset.get("x")) - shift))
        extent.set("cx", str(width + extra))
        changed = True
    return changed


def _spell_out_list_numbers(root: etree._Element) -> bool:
    """Write each list item's real number where a list uses startAt.

    PowerPoint reads a repeated startAt="2" as "this list starts at 2" and counts 2, 3, 4. The
    office engine restarts at 2 on every item, showing 2, 2, 2. Blank items are not counted.
    It runs on temporary render decks only.
    """
    changed = False
    for body in _text_bodies(root):
        previous: dict[int, tuple[str, str, int]] = {}  # level -> scheme, its startAt, last number
        for paragraph in body.findall(A + "p"):
            props = paragraph.find(A + "pPr")
            level = int(props.get("lvl", "0")) if props is not None else 0
            numbering = props.find(A + "buAutoNum") if props is not None else None
            if not _paragraph_text(paragraph).strip():
                continue
            for deeper in [key for key in previous if key > level]:
                del previous[deeper]
            if numbering is None:
                previous.pop(level, None)
                continue
            scheme, start = numbering.get("type", ""), numbering.get("startAt")
            last = previous.get(level)
            first = int(start) if start else 1
            number = last[2] + 1 if last and last[0] == scheme and last[1] == (start or "") else first
            previous[level] = (scheme, start or "", number)
            if start and number != first:
                numbering.set("startAt", str(number))
                changed = True
    return changed


def _prepare_for_rendering(ordered: list[tuple[Part, str | None]]) -> None:
    seen: set[str] = set()
    for part, _reuse in ordered:
        layout = part.related("/slideLayout")
        master = layout.related("/slideMaster") if layout is not None else None
        for item in (part, layout, master):
            if item is None or item.name in seen:
                continue
            seen.add(item.name)
            if b'wrap="none"' in item.blob or b"startAt=" in item.blob:
                xml = item.xml()
                widened = _widen_unwrapped_text(xml)
                numbered = _spell_out_list_numbers(xml)
                if widened or numbered:
                    item.set_xml(xml)


def _prune_unused_masters(builder: _Builder, ordered: list[tuple[Part, str | None]]) -> None:
    """Drop layouts and masters no kept slide uses. Render-only decks load far faster this way."""
    used_layouts = set()
    for part, _reuse in ordered:
        layout = part.related("/slideLayout")
        if layout is not None:
            used_layouts.add(layout.name)
    master_list = builder.root.find(P + "sldMasterIdLst")
    if master_list is None:
        return
    for node in list(master_list):
        rel = builder.pres.rels.get(node.get(R + "id"))
        master = builder.pres.target_part(rel) if rel else None
        if master is None:
            continue
        xml = master.xml()
        layouts = xml.find(P + "sldLayoutIdLst")
        kept = 0
        if layouts is not None:
            for layout_node in list(layouts):
                layout_rid = layout_node.get(R + "id")
                layout_rel = master.rels.get(layout_rid)
                layout = master.target_part(layout_rel) if layout_rel else None
                if layout is not None and layout.name in used_layouts:
                    kept += 1
                else:
                    layouts.remove(layout_node)
                    master.rels.pop(layout_rid, None)
            master.set_xml(xml)
        if kept == 0 and len(master_list) > 1:
            master_list.remove(node)
            del builder.pres.rels[node.get(R + "id")]


def build_deck(
    base_path: str | Path,
    specs: list[SlideSpec],
    out_path: str | Path,
    *,
    first_slide_number: int | None = None,
    render_only: bool = False,
) -> BuildReport:
    """Write a new PPTX holding `specs`, in order, using `base_path` as the host deck.

    `render_only` strips what a preview does not need, such as unused masters. Such
    a file is a temporary rendering input and is never offered to the user.
    """
    if not specs:
        raise ViewerError("A presentation must keep at least one slide.")
    report = BuildReport()
    builder = _Builder(str(base_path), report)
    try:
        ordered = [builder.slide_part_for(spec) for spec in specs]
        for (part, _reuse), spec in zip(ordered, specs):
            if spec.edits:
                xml = part.xml()
                _apply_edits_to_root(xml, spec.edits)
                part.set_xml(xml)
        builder.finish(ordered, specs)
        if render_only:
            _prune_unused_masters(builder, ordered)
            _prepare_for_rendering(ordered)
        if first_slide_number is not None:
            builder.root.set("firstSlideNum", str(first_slide_number))
        _set_main_content_type(builder.pres, Path(out_path).suffix.lower())
        builder.pres.set_xml(builder.root)
        builder.target.save(out_path)
    finally:
        builder.close()
    return report


def _set_main_content_type(pres: Part, suffix: str) -> None:
    if suffix == ".pptx" and "slideshow.main" in pres.content_type:
        pres.content_type = CT_PRES
    elif suffix == ".pptx" and "macroEnabled" in pres.content_type:
        pres.content_type = CT_PRES


def scan_fonts(path: str | Path) -> set[str]:
    """Typeface names a deck asks for, excluding theme references such as +mj-lt."""
    pattern = re.compile(rb'<a:(?:latin|ea|cs|sym)\s[^>]*typeface="([^"]+)"')
    fonts: set[str] = set()
    with Package(path) as package:
        for part in package.reachable():
            if part.content_type.endswith("+xml") and (
                "slide" in part.content_type or "theme" in part.content_type
            ):
                for match in pattern.finditer(part.blob):
                    name = match.group(1).decode("utf-8", "replace")
                    if not name.startswith("+"):
                        fonts.add(name)
    return fonts
