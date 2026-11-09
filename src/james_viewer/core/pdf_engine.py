"""PDF reading through PDFium and page operations through pikepdf/qpdf."""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from .errors import CorruptFile, ViewerError, PasswordRequired, PermissionDenied, WrongPassword

# PDFium is not thread-safe, even across separate documents. Every call goes through this lock.
PDFIUM_LOCK = threading.RLock()


@dataclass(frozen=True)
class PdfAccess:
    encrypted: bool = False
    owner_verified: bool = False
    can_extract: bool = True
    can_assemble: bool = True
    can_modify: bool = True
    can_annotate: bool = True

    @property
    def fully_open(self) -> bool:
        return self.can_extract and self.can_assemble and self.can_modify

    @property
    def may_remove_protection(self) -> bool:
        """Removing protection is allowed only if it would not lift any restriction the author set."""
        return self.encrypted and (self.owner_verified or self.fully_open)


def read_access(path: str | Path, password: str | None) -> PdfAccess:
    """Open with the given password and report what it permits. Never tries any other password."""
    try:
        with pikepdf.open(path, password=password or "") as pdf:
            if not pdf.is_encrypted:
                return PdfAccess()
            allow = pdf.allow
            owner = bool(pdf.owner_password_matched) and bool(password)
            return PdfAccess(
                encrypted=True,
                owner_verified=owner,
                can_extract=owner or bool(allow.extract),
                # The PDF spec's general "modify" bit also permits assembling pages.
                can_assemble=owner or bool(allow.modify_assembly) or bool(allow.modify_other),
                can_modify=owner or bool(allow.modify_other),
                can_annotate=owner or bool(allow.modify_annotation),
            )
    except pikepdf.PasswordError as exc:
        if password:
            raise WrongPassword("That password is not correct.") from exc
        raise PasswordRequired(f"{Path(path).name} is protected with a password.") from exc
    except (pikepdf.PdfError, ValueError, OSError) as exc:
        raise CorruptFile(f"{Path(path).name} is a damaged PDF and cannot be opened ({exc}).") from exc


class PdfiumDocuments:
    """Open PDFium documents, keyed by file. Decrypted files are only ever opened with their password."""

    def __init__(self):
        self._docs: dict[str, object] = {}

    def get(self, path: str, password: str | None = None):
        import pypdfium2

        with PDFIUM_LOCK:
            document = self._docs.get(path)
            if document is None:
                document = pypdfium2.PdfDocument(path, password=password)
                try:
                    document.init_forms()
                except Exception:
                    pass
                self._docs[path] = document
            return document

    def page_sizes(self, path: str, password: str | None = None) -> list[tuple[float, float]]:
        with PDFIUM_LOCK:
            document = self.get(path, password)
            sizes = []
            for index in range(len(document)):
                page = document[index]
                sizes.append(page.get_size())
                page.close()
            return sizes

    def render(self, path: str, password: str | None, index: int, width_px: int, height_px: int):
        """Return (buffer, width, height, stride) in BGRx order, for Qt's RGB32 format."""
        with PDFIUM_LOCK:
            document = self.get(path, password)
            page = document[index]
            try:
                width, height = page.get_size()
                scale = min(width_px / max(width, 1), height_px / max(height, 1))
                bitmap = page.render(scale=scale, may_draw_forms=True, prefer_bgrx=True, rev_byteorder=False)
                channels = bitmap.n_channels
                data = bytes(bitmap.buffer)
                return data, bitmap.width, bitmap.height, bitmap.stride, channels
            finally:
                page.close()

    def text_page(self, path: str, password: str | None, index: int):
        with PDFIUM_LOCK:
            document = self.get(path, password)
            page = document[index]
            return page, page.get_textpage()

    def close_all(self) -> None:
        with PDFIUM_LOCK:
            for document in self._docs.values():
                try:
                    document.close()
                except Exception:
                    pass
            self._docs.clear()


@dataclass(frozen=True)
class PdfPageSpec:
    source: str
    password: str | None
    index: int
    annotations: tuple[str, ...] = ()  # JSON, added when the page is written


def _atomic_save(pdf: pikepdf.Pdf, out_path: str | Path, encryption) -> None:
    out_path = Path(out_path)
    temporary = out_path.with_name(f".{out_path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        pdf.save(
            temporary,
            encryption=encryption,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            preserve_pdfa=True,
        )
        os.replace(temporary, out_path)
    finally:
        temporary.unlink(missing_ok=True)


def _page_key(obj) -> tuple[int, int] | None:
    try:
        return tuple(obj.objgen) if obj.is_indirect else None
    except (AttributeError, ValueError, TypeError):
        return None


def _destination_page(pdf: pikepdf.Pdf, destination, named: dict) -> tuple[int, int] | None:
    if destination is None:
        return None
    if isinstance(destination, (pikepdf.Name, pikepdf.String, str)):
        destination = named.get(str(destination))
        if destination is None:
            return None
    if isinstance(destination, pikepdf.Dictionary) and "/D" in destination:
        destination = destination.D
    if isinstance(destination, (pikepdf.Array,)) and len(destination) > 0:
        return _page_key(destination[0])
    return None


def _named_destinations(pdf: pikepdf.Pdf) -> dict:
    named: dict = {}
    root = pdf.Root
    if "/Dests" in root:
        for key, value in root.Dests.items():
            named[str(key)] = value
    if "/Names" in root and "/Dests" in root.Names:
        try:
            for key, value in pikepdf.NameTree(root.Names.Dests).items():
                named[str(key)] = value
        except (ValueError, TypeError, pikepdf.PdfError):
            pass
    return named


def _prune_after_page_removal(pdf: pikepdf.Pdf, removed: set[tuple[int, int]]) -> None:
    """Stop anything in the file pointing at removed pages, so their content does not stay inside."""
    named = _named_destinations(pdf)
    root = pdf.Root

    def outline_is_dead(item) -> bool:
        destination = item.destination
        if destination is None and item.action is not None and item.action.get("/S") == pikepdf.Name.GoTo:
            destination = item.action.get("/D")
        return _destination_page(pdf, destination, named) in removed

    def prune_items(items) -> None:
        for item in list(items):
            prune_items(item.children)
            if outline_is_dead(item):
                position = items.index(item)
                items.remove(item)
                for offset, child in enumerate(item.children):
                    items.insert(position + offset, child)

    if "/Outlines" in root:
        with pdf.open_outline() as outline:
            prune_items(outline.root)

    if "/Dests" in root:
        del root.Dests
    if "/Names" in root and "/Dests" in root.Names:
        del root.Names.Dests
    if "/OpenAction" in root:
        action = root.OpenAction
        destination = action.get("/D") if isinstance(action, pikepdf.Dictionary) else action
        if _destination_page(pdf, destination, named) in removed:
            del root.OpenAction
    if "/StructTreeRoot" in root:
        del root.StructTreeRoot
    if "/MarkInfo" in root:
        del root.MarkInfo

    for page in pdf.pages:
        annotations = page.obj.get("/Annots")
        if annotations is None:
            continue
        kept = []
        for annotation in annotations:
            destination = annotation.get("/Dest")
            if destination is None and "/A" in annotation and annotation.A.get("/S") == pikepdf.Name.GoTo:
                destination = annotation.A.get("/D")
            if _destination_page(pdf, destination, named) not in removed:
                kept.append(annotation)
        if len(kept) != len(annotations):
            page.obj.Annots = pdf.make_indirect(pikepdf.Array(kept))

    if "/AcroForm" in root and "/Fields" in root.AcroForm:
        def live(field) -> bool:
            if _page_key(field.get("/P")) in removed:
                return False
            if "/Kids" in field:
                kids = [kid for kid in field.Kids if live(kid)]
                field.Kids = pikepdf.Array(kids)
                return len(kids) > 0
            return True

        root.AcroForm.Fields = pikepdf.Array([field for field in root.AcroForm.Fields if live(field)])


def write_pdf(base: PdfPageSpec, specs: list[PdfPageSpec], out_path: str | Path, *, encryption=False, paint=None) -> None:
    """Write `specs` in order to a new PDF. `base` supplies the document-level structure."""
    if not specs:
        raise ViewerError("A document must keep at least one page.")
    foreign: dict[str, pikepdf.Pdf] = {}
    try:
        with pikepdf.open(base.source, password=base.password or "") as pdf:
            originals = list(pdf.pages)
            keys = [_page_key(page.obj) for page in originals]
            order = [(spec.source, spec.index) for spec in specs]
            identity = [(base.source, index) for index in range(len(originals))]
            if order != identity:
                kept_base = {spec.index for spec in specs if spec.source == base.source}
                removed = {keys[index] for index in range(len(originals)) if index not in kept_base}
                pages = []
                seen: set[int] = set()
                for spec in specs:
                    if spec.source == base.source:
                        pages.append(("base", spec.index, spec.index in seen))
                        seen.add(spec.index)
                    else:
                        if spec.source not in foreign:
                            foreign[spec.source] = pikepdf.open(spec.source, password=spec.password or "")
                        pages.append((spec.source, spec.index, False))

                count = len(pdf.pages)
                for origin, index, duplicate in pages:
                    if origin == "base":
                        if duplicate:
                            pdf.pages.append(originals[index])
                        else:
                            pdf.pages.append(originals[index])
                    else:
                        pdf.pages.append(foreign[origin].pages[index])
                del pdf.pages[:count]
                # Appending a page already in the file makes a copy, so restore the original
                # objects for each page's first use. Outlines and links then still resolve.
                _reattach_first_uses(pdf, pages, originals)
                if removed:
                    _prune_after_page_removal(pdf, removed)
                if "/PageLabels" in pdf.Root:
                    del pdf.Root.PageLabels
            if any(spec.annotations for spec in specs):
                from .pdf_annotations import add_annotations

                for position, spec in enumerate(specs):
                    if spec.annotations:
                        add_annotations(pdf, pdf.pages[position], list(spec.annotations), paint)
            _atomic_save(pdf, out_path, encryption)
    except pikepdf.PasswordError as exc:
        raise WrongPassword("A password needed for this operation is no longer valid.") from exc
    except (pikepdf.PdfError, OSError) as exc:
        raise ViewerError(f"The PDF could not be written: {exc}") from exc
    finally:
        for document in foreign.values():
            document.close()


def _reattach_first_uses(pdf: pikepdf.Pdf, pages: list, originals: list) -> None:
    for position, (origin, index, duplicate) in enumerate(pages):
        if origin == "base" and not duplicate:
            pdf.pages[position] = originals[index]


def merge_pdfs(inputs: list[tuple[str, str | None]], names: list[str], out_path: str | Path) -> None:
    """Join PDFs into a new file, keeping form fields and giving each input an outline entry."""
    temporary = Path(out_path).with_name(f".{Path(out_path).stem}.{uuid.uuid4().hex[:8]}.merge.pdf")
    arguments = ["pikepdf", "--empty", "--pages"]
    for path, password in inputs:
        arguments.append(path)
        if password:
            arguments.append(f"--password={password}")
        arguments.append("1-z")
    arguments += ["--", str(temporary)]
    try:
        pikepdf.Job(arguments).run()
        offsets = []
        total = 0
        for path, password in inputs:
            with pikepdf.open(path, password=password or "") as source:
                offsets.append(total)
                total += len(source.pages)
        with pikepdf.open(temporary) as merged:
            with merged.open_outline() as outline:
                for (path, password), offset, name in zip(inputs, offsets, names):
                    top = pikepdf.OutlineItem(name, offset)
                    top.children.extend(_copied_outline(path, password, offset, merged))
                    outline.root.append(top)
            _atomic_save(merged, out_path, False)
    except pikepdf.PasswordError as exc:
        raise WrongPassword("A password needed for this merge is not correct.") from exc
    except pikepdf.JobUsageError as exc:
        raise ViewerError(f"The PDFs could not be merged: {exc}") from exc
    except (pikepdf.PdfError, OSError) as exc:
        raise ViewerError(f"The PDFs could not be merged: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _copied_outline(path: str, password: str | None, offset: int, merged: pikepdf.Pdf) -> list:
    """Rebuild a source file's outline against the merged page numbers."""
    with pikepdf.open(path, password=password or "") as source:
        if "/Outlines" not in source.Root:
            return []
        keys = {_page_key(page.obj): number for number, page in enumerate(source.pages)}
        named = _named_destinations(source)

        def convert(items) -> list:
            result = []
            for item in items:
                destination = item.destination
                if destination is None and item.action is not None and item.action.get("/S") == pikepdf.Name.GoTo:
                    destination = item.action.get("/D")
                number = keys.get(_destination_page(source, destination, named))
                children = convert(item.children)
                if number is None:
                    result.extend(children)
                    continue
                copy = pikepdf.OutlineItem(str(item.title), number + offset)
                copy.children.extend(children)
                result.append(copy)
            return result

        with source.open_outline() as outline:
            return convert(outline.root)


def blank_page_pdf(out_path: str | Path, width: float, height: float) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(width, height))
    pdf.save(out_path)


def remove_protection(path: str, password: str | None, access: PdfAccess, out_path: str | Path) -> None:
    if not access.encrypted:
        raise ViewerError("This PDF has no protection to remove.")
    if not access.may_remove_protection:
        raise PermissionDenied(
            "This PDF restricts editing or copying. Only the author's owner password can authorize "
            "an unprotected copy."
        )
    with pikepdf.open(path, password=password or "") as pdf:
        _atomic_save(pdf, out_path, False)


def copy_file(source: str | Path, out_path: str | Path) -> None:
    out_path = Path(out_path)
    temporary = out_path.with_name(f".{out_path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, out_path)
    finally:
        temporary.unlink(missing_ok=True)
