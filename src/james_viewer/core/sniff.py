"""Identify a document by its content rather than trusting the file extension."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .errors import CorruptFile, UnsupportedFile

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

MARKDOWN_EXTENSIONS = {".md", ".markdown"}
WORD_EXTENSIONS = {".doc", ".docx", ".docm"}
OPEN_EXTENSIONS = {".pdf", ".ppt", ".pptx"} | WORD_EXTENSIONS | MARKDOWN_EXTENSIONS
FILE_FILTER = (
    "Documents (*.pdf *.ppt *.pptx *.doc *.docx *.docm *.md *.markdown);;PDF (*.pdf);;PowerPoint (*.ppt *.pptx);;"
    "Word (*.doc *.docx *.docm);;Markdown (*.md *.markdown)"
)
MERGE_FILTER = "PDF and PowerPoint (*.pdf *.ppt *.pptx);;PDF (*.pdf);;PowerPoint (*.ppt *.pptx)"


@dataclass(frozen=True)
class FileKind:
    format: str  # pdf, pptx, ppt, docx, doc or md
    encrypted: bool

    @property
    def family(self) -> str:
        return {"pdf": "pdf", "md": "markdown", "docx": "word", "doc": "word"}.get(self.format, "office")


def sniff(path: str | Path) -> FileKind:
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            head = handle.read(2048)
    except FileNotFoundError as exc:
        raise CorruptFile(f"{path.name} could not be found.") from exc
    except PermissionError as exc:
        raise CorruptFile(f"Windows denied access to {path.name}. It may be open elsewhere.") from exc
    except OSError as exc:
        raise CorruptFile(f"{path.name} could not be read ({exc}).") from exc

    if path.suffix.lower() in MARKDOWN_EXTENSIONS:
        return FileKind("md", False)  # plain text has no signature, and an empty note is still valid
    if not head:
        raise CorruptFile(f"{path.name} is empty.")
    if b"%PDF-" in head[:1024]:
        return FileKind("pdf", _pdf_is_encrypted(path))
    if head.startswith(ZIP_MAGIC):
        return _sniff_zip(path)
    if head.startswith(OLE_MAGIC):
        return _sniff_ole(path)
    if path.suffix.lower() in OPEN_EXTENSIONS:
        raise CorruptFile(f"{path.name} is damaged: its contents do not match a {path.suffix} file.")
    raise UnsupportedFile(f"{path.name} is not a PDF, PowerPoint, Word or Markdown file.")


def _pdf_is_encrypted(path: Path) -> bool:
    import pikepdf

    try:
        with pikepdf.open(path) as pdf:
            return pdf.is_encrypted
    except pikepdf.PasswordError:
        return True
    except (pikepdf.PdfError, ValueError, OSError) as exc:
        raise CorruptFile(f"{path.name} is a damaged PDF and cannot be opened ({exc}).") from exc


def _sniff_zip(path: Path) -> FileKind:
    try:
        with zipfile.ZipFile(path) as archive:
            names = {name.lower(): name for name in archive.namelist()}
            types_name = names.get("[content_types].xml")
            if types_name is None:
                raise UnsupportedFile(f"{path.name} is a ZIP archive, not a PowerPoint or Word file.")
            types = etree.fromstring(archive.read(types_name))
    except zipfile.BadZipFile as exc:
        raise CorruptFile(f"{path.name} is damaged and cannot be opened.") from exc
    except etree.XMLSyntaxError as exc:
        raise CorruptFile(f"{path.name} is damaged: its package index cannot be read.") from exc

    for node in types:
        content_type = node.get("ContentType") or ""
        if "presentationml.presentation.main" in content_type or "presentationml.slideshow.main" in content_type:
            return FileKind("pptx", False)
        if "ms-powerpoint.presentation.macroenabled" in content_type:
            return FileKind("pptx", False)
        if "wordprocessingml.document.main" in content_type or "ms-word.document.macroenabled.main" in content_type:
            return FileKind("docx", False)
    raise UnsupportedFile(f"{path.name} is an Office file, but not a PowerPoint presentation or Word document.")


def _sniff_ole(path: Path) -> FileKind:
    import olefile

    try:
        with olefile.OleFileIO(str(path)) as ole:
            if ole.exists("EncryptedPackage"):
                # A locked DOCX and a locked PPTX look the same until opened, so the name decides.
                return FileKind("docx" if path.suffix.lower() in WORD_EXTENSIONS else "pptx", True)
            if ole.exists("PowerPoint Document"):
                return FileKind("ppt", _ppt_is_encrypted(path))
            if ole.exists("WordDocument"):
                return FileKind("doc", _ppt_is_encrypted(path))
    except OSError as exc:
        raise CorruptFile(f"{path.name} is damaged and cannot be opened ({exc}).") from exc
    raise UnsupportedFile(f"{path.name} is an older Office file, but not a PowerPoint presentation or Word document.")


def _ppt_is_encrypted(path: Path) -> bool:
    import msoffcrypto

    try:
        with open(path, "rb") as handle:
            return bool(msoffcrypto.OfficeFile(handle).is_encrypted())
    except Exception as exc:  # msoffcrypto raises assorted parser errors on bad streams
        raise CorruptFile(f"{path.name} is damaged: its contents cannot be read ({exc}).") from exc
