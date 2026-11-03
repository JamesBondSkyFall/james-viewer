"""A small Open Packaging Conventions layer.

Parts that an operation does not touch are written back byte for byte, which is
the main reason this exists instead of re-serialising through a document model.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

from .errors import CorruptFile

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_STORED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "mp4", "m4v", "m4a", "mp3", "wmv", "wma", "avi", "mov",
    "zip", "xlsx", "docx", "pptx", "bin", "odttf", "fntdata",
}


@dataclass
class Rel:
    id: str
    type: str
    target: str  # the raw Target value, kept so unknown forms survive a round trip
    external: bool = False


class Part:
    def __init__(self, package: "Package", name: str, content_type: str, blob: bytes | None = None):
        self.package = package
        self.name = name
        self.content_type = content_type
        self.rels: dict[str, Rel] = {}
        self._blob = blob
        self._zip_name: str | None = None

    @property
    def blob(self) -> bytes:
        if self._blob is None:
            self._blob = self.package.read_entry(self._zip_name)
        return self._blob

    @blob.setter
    def blob(self, value: bytes) -> None:
        self._blob = value

    def xml(self) -> etree._Element:
        return etree.fromstring(self.blob)

    def set_xml(self, root: etree._Element) -> None:
        self._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def target_part(self, rel: Rel) -> "Part | None":
        if rel.external:
            return None
        return self.package.resolve(self.name, rel.target)

    def rels_of_type(self, suffix: str) -> list[Rel]:
        return [rel for rel in self.rels.values() if rel.type.endswith(suffix)]

    def related(self, suffix: str) -> "Part | None":
        for rel in self.rels_of_type(suffix):
            part = self.target_part(rel)
            if part is not None:
                return part
        return None

    def next_rid(self) -> str:
        used = {int(m.group(1)) for rid in self.rels if (m := re.fullmatch(r"rId(\d+)", rid))}
        value = 1
        while value in used:
            value += 1
        return f"rId{value}"

    def relate_to(self, target: "Part", rel_type: str, rid: str | None = None) -> str:
        rid = rid or self.next_rid()
        self.rels[rid] = Rel(rid, rel_type, target.name, False)
        return rid


def _rels_entry(name: str) -> str:
    directory, base = posixpath.split(name)
    return posixpath.join(directory, "_rels", base + ".rels").lstrip("/")


class Package:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.parts: dict[str, Part] = {}
        self.root_rels: dict[str, Rel] = {}
        self.defaults: dict[str, str] = {}
        self._lower_index: dict[str, str] = {}
        self._zip: zipfile.ZipFile | None = None
        if path:
            self._load()

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def read_entry(self, name: str) -> bytes:
        return self._zip.read(name)

    def _load(self) -> None:
        try:
            self._zip = zipfile.ZipFile(self.path)
            names = self._zip.namelist()
            lower = {n.lower(): n for n in names}
            types_name = lower.get("[content_types].xml")
            if types_name is None:
                raise CorruptFile(f"{self.path.name} is not a valid Office package.")
            types = etree.fromstring(self._zip.read(types_name))
        except (zipfile.BadZipFile, etree.XMLSyntaxError, EOFError, OSError) as exc:
            raise CorruptFile(f"{self.path.name} is damaged and cannot be read ({exc}).") from exc

        overrides: dict[str, str] = {}
        for node in types:
            tag = etree.QName(node).localname
            if tag == "Default":
                self.defaults[node.get("Extension").lower()] = node.get("ContentType")
            elif tag == "Override":
                overrides[node.get("PartName").lower()] = node.get("ContentType")

        for entry in names:
            if entry.endswith("/") or entry.lower() == "[content_types].xml":
                continue
            if entry.lower().endswith(".rels") and "_rels/" in entry.lower():
                continue
            name = "/" + entry
            extension = posixpath.splitext(entry)[1].lstrip(".").lower()
            content_type = overrides.get(name.lower()) or self.defaults.get(extension)
            if content_type is None:
                continue
            part = Part(self, name, content_type)
            part._zip_name = entry
            self.parts[name] = part
            self._lower_index[name.lower()] = name

        root_rels = lower.get("_rels/.rels")
        if root_rels is None:
            raise CorruptFile(f"{self.path.name} has no package relationships.")
        self.root_rels = self._parse_rels(self._zip.read(root_rels))
        for part in self.parts.values():
            entry = lower.get(_rels_entry(part.name).lower())
            if entry is not None:
                part.rels = self._parse_rels(self._zip.read(entry))

    @staticmethod
    def _parse_rels(data: bytes) -> dict[str, Rel]:
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError as exc:
            raise CorruptFile(f"A relationship list is damaged ({exc}).") from exc
        rels = {}
        for node in root:
            rel = Rel(
                node.get("Id"),
                node.get("Type"),
                node.get("Target"),
                (node.get("TargetMode") or "").lower() == "external",
            )
            rels[rel.id] = rel
        return rels

    def resolve(self, base: str, target: str) -> Part | None:
        if target.startswith("/"):
            name = target
        else:
            name = posixpath.normpath(posixpath.join(posixpath.dirname(base), target))
        for candidate in (name, unquote(name)):
            found = self._lower_index.get(candidate.lower())
            if found:
                return self.parts[found]
        return None

    def add_part(self, part: Part) -> Part:
        self.parts[part.name] = part
        self._lower_index[part.name.lower()] = part.name
        return part

    def main_part(self) -> Part:
        for rel in self.root_rels.values():
            if rel.type.endswith("/officeDocument"):
                part = self.resolve("/", rel.target)
                if part is not None:
                    return part
        raise CorruptFile("This package has no main document part.")

    def next_partname(self, like: str) -> str:
        """Allocate a free part name in the same folder, in the style of `like`."""
        directory, base = posixpath.split(like)
        stem, extension = posixpath.splitext(base)
        stem = re.sub(r"\d+$", "", stem) or "part"
        pattern = re.compile(re.escape(f"{directory}/{stem}".lower()) + r"(\d+)" + re.escape(extension.lower()) + "$")
        used = [int(m.group(1)) for n in self._lower_index if (m := pattern.match(n))]
        value = (max(used) + 1) if used else 1
        return f"{directory}/{stem}{value}{extension}"

    def reachable(self) -> list[Part]:
        seen: dict[str, Part] = {}
        stack: list[Part] = []
        for rel in self.root_rels.values():
            if not rel.external:
                part = self.resolve("/", rel.target)
                if part is not None:
                    stack.append(part)
        while stack:
            part = stack.pop()
            if part.name in seen:
                continue
            seen[part.name] = part
            for rel in part.rels.values():
                if not rel.external:
                    target = part.target_part(rel)
                    if target is not None and target.name not in seen:
                        stack.append(target)
        return list(seen.values())

    def save(self, path: str | Path) -> None:
        parts = self.reachable()
        parts.sort(key=lambda p: p.name)
        lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            f'<Types xmlns="{CT_NS}">',
        ]
        used_extensions = {posixpath.splitext(p.name)[1].lstrip(".").lower() for p in parts}
        used_extensions.add("rels")
        for extension, content_type in sorted(self.defaults.items()):
            if extension in used_extensions:
                lines.append(f"<Default Extension={quoteattr(extension)} ContentType={quoteattr(content_type)}/>")
        if "rels" not in self.defaults:
            lines.append(
                '<Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            )
        for part in parts:
            extension = posixpath.splitext(part.name)[1].lstrip(".").lower()
            if self.defaults.get(extension) != part.content_type:
                lines.append(
                    f"<Override PartName={quoteattr(part.name)} ContentType={quoteattr(part.content_type)}/>"
                )
        lines.append("</Types>")

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            out.writestr("[Content_Types].xml", "".join(lines).encode("utf-8"))
            out.writestr("_rels/.rels", _serialize_rels(self.root_rels))
            for part in parts:
                extension = posixpath.splitext(part.name)[1].lstrip(".").lower()
                compress = zipfile.ZIP_STORED if extension in _STORED_EXTENSIONS else zipfile.ZIP_DEFLATED
                out.writestr(part.name.lstrip("/"), part.blob, compress_type=compress)
                if part.rels:
                    out.writestr(_rels_entry(part.name), _serialize_rels(part.rels))


def _serialize_rels(rels: dict[str, Rel]) -> bytes:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<Relationships xmlns="{REL_NS}">',
    ]
    for rel in rels.values():
        mode = ' TargetMode="External"' if rel.external else ""
        lines.append(
            f"<Relationship Id={quoteattr(rel.id)} Type={quoteattr(rel.type)} "
            f"Target={quoteattr(rel.target)}{mode}/>"
        )
    lines.append("</Relationships>")
    return "".join(lines).encode("utf-8")


__all__ = ["Package", "Part", "Rel", "escape"]
