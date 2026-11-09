"""An open document: its pages as immutable values, undo history, rendering and saving.

Each edit produces a new tuple of Page values, so undo is restoring an earlier tuple and
nothing has to be re-rendered that was rendered before.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QFontDatabase

from ..core import inspect, paths
from ..core.errors import CorruptFile, ViewerError, PasswordRequired, PermissionDenied
from ..core.office_engine import CHUNK_SIZE, FIRST_CHUNK
from ..core.pdf_engine import (
    PdfAccess, PdfPageSpec, blank_page_pdf, copy_file, read_access, remove_protection, write_pdf,
)
from ..core.pptx_engine import DeckInfo, SlideSpec, TextBlock, build_deck, list_text_blocks, read_deck_info
from ..core.security import decrypt_doc, decrypt_office, decrypt_ppt, encrypt_pptx
from ..core.sniff import FileKind, sniff
from .services import RenderRef, Workspace

RENDER_VERSION = "26.8.2-3"
EMU_PER_POINT = 12700
_uids = itertools.count(1)


@dataclass(frozen=True)
class Page:
    uid: int
    source: str
    index: int
    layout: str | None = None
    edits: tuple[tuple[int, str], ...] = ()
    hidden: bool = False
    numbered: bool = False

    def spec(self) -> SlideSpec:
        return SlideSpec(self.source, self.index, self.layout, self.edits)


def new_page(source: str, index: int, **values) -> Page:
    return Page(next(_uids), source, index, **values)


@dataclass
class Opened:
    """What the background open step prepared, handed to a Document on the GUI thread."""

    path: Path
    kind: FileKind
    password: str | None
    access: PdfAccess
    raw_copy: str
    source_copy: str
    deck: DeckInfo | None
    pages: tuple[Page, ...]
    notes: list[inspect.Note]


def prepare(workspace: Workspace, path: Path, password: str | None):
    """Runs on a worker thread. Returns Opened, or a MarkdownPage for Markdown files.

    Raises PasswordRequired or WrongPassword for the UI to handle.
    """
    path = Path(path)
    kind = sniff(path)
    if kind.format == "md":
        from ..core.markdown_engine import parse_markdown

        return parse_markdown(path)
    folder = workspace.session.new_dir("doc")
    raw_copy = folder / ("original" + path.suffix.lower())
    try:
        shutil.copyfile(path, raw_copy)
    except OSError as exc:
        raise CorruptFile(f"{path.name} could not be read ({exc}).") from exc

    if kind.format == "pdf":
        access = read_access(raw_copy, password)
        try:
            sizes = workspace.pdfium.page_sizes(str(raw_copy), password)
        except Exception as exc:  # PDFium reports damage through its own error types
            raise CorruptFile(f"{path.name} is a damaged PDF and cannot be displayed ({exc}).") from exc
        if not sizes:
            raise CorruptFile(f"{path.name} has no pages.")
        pages = []
        for index, (width, height) in enumerate(sizes):
            page = new_page(str(raw_copy), index)
            workspace.renders[render_key(page)] = RenderRef(str(raw_copy), index, password, width, height)
            pages.append(page)
        return Opened(path, kind, password, access, str(raw_copy), str(raw_copy), None, tuple(pages),
                      inspect.inspect_pdf(raw_copy, password))

    if kind.family == "word":
        source = raw_copy
        if kind.encrypted:
            if not password:
                raise PasswordRequired(f"{path.name} is protected with a password.")
            source = folder / f"unlocked.{kind.format}"
            (decrypt_doc if kind.format == "doc" else decrypt_office)(raw_copy, password, source)
        elif raw_copy.suffix != f".{kind.format}":
            source = folder / f"document.{kind.format}"
            shutil.copyfile(raw_copy, source)
        return Opened(path, kind, password, PdfAccess(encrypted=kind.encrypted), str(raw_copy), str(source), None, (), [])

    source = raw_copy
    if kind.encrypted:
        if not password:
            raise PasswordRequired(f"{path.name} is protected with a password.")
        if kind.format == "ppt":
            source = folder / "unlocked.ppt"
            decrypt_ppt(raw_copy, password, source)
        else:
            source = folder / "unlocked.pptx"
            decrypt_office(raw_copy, password, source)
    elif kind.format == "pptx" and raw_copy.suffix != ".pptx":
        source = folder / "document.pptx"
        shutil.copyfile(raw_copy, source)

    if kind.format == "pptx":
        deck = read_deck_info(source)
        if not deck.slides:
            raise CorruptFile(f"{path.name} has no slides.")
        pages = tuple(
            new_page(str(source), slide.index, hidden=slide.hidden, numbered=slide.has_slide_number)
            for slide in deck.slides
        )
        return Opened(path, kind, password, PdfAccess(encrypted=kind.encrypted), str(raw_copy), str(source),
                      deck, pages, inspect.inspect_pptx(source))

    return Opened(path, kind, password, PdfAccess(encrypted=kind.encrypted), str(raw_copy), str(source),
                  None, (), inspect.inspect_ppt())


def render_key(page: Page, number: int | None = None, size: tuple[int, int] | None = None) -> tuple:
    return (page.source, page.index, page.layout, page.edits, number, size)


class Document(QObject):
    pages_changed = Signal()
    renders_changed = Signal()
    state_changed = Signal()
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, workspace: Workspace, opened: Opened, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.path = opened.path
        self.name = opened.path.name
        self.format = opened.kind.format
        self.family = opened.kind.family
        self.password = opened.password
        self.access = opened.access
        self.protected = opened.kind.encrypted
        self.raw_copy = opened.raw_copy
        self.base_path = opened.source_copy
        self.deck = opened.deck
        self.pages: tuple[Page, ...] = opened.pages
        self.notes = list(opened.notes)
        self._history: list[tuple[str, tuple[Page, ...]]] = []
        self._future: list[tuple[str, tuple[Page, ...]]] = []
        self._saved_pages = opened.pages
        self._saved_format = self.format
        self._opened_pages = opened.pages
        self._source_sizes: dict[str, tuple[int, int] | None] = {}
        self._status_text = ""
        self.current = 0
        self.preparing = self.family == "office"
        self.converted = False
        self.ppt_as_pptx: str | None = None
        self.closed = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(350)
        self._refresh_timer.timeout.connect(self._refresh_renders)
        self._refresh_lock = threading.Lock()

    # -- descriptions -----------------------------------------------------------------------

    @property
    def noun(self) -> str:
        return "page" if self.family == "pdf" else "slide"

    @property
    def dirty(self) -> bool:
        return self.pages != self._saved_pages or self.format != self._saved_format

    @property
    def pristine(self) -> bool:
        """True while the content is still exactly what was opened, so its bytes can be copied."""
        return self.pages == self._opened_pages and not self.converted

    def status_text(self) -> str:
        return self._status_text

    @property
    def can_edit_pages(self) -> bool:
        if self.family == "pdf":
            return self.access.can_assemble
        return not (self.deck and self.deck.modify_protected)

    @property
    def can_copy_text(self) -> bool:
        return self.access.can_extract

    @property
    def edit_block_reason(self) -> str:
        if self.family == "pdf" and not self.access.can_assemble:
            return "The author of this PDF does not allow its pages to be changed."
        if self.deck and self.deck.modify_protected:
            return "The author marked this presentation read-only with a modify password."
        return ""

    @property
    def can_edit_text(self) -> bool:
        return self.family == "office" and self.can_edit_pages

    @property
    def can_annotate(self) -> bool:
        return self.family == "pdf" and self.access.can_annotate

    def annotate(self, position: int, annotation: dict, label: str) -> None:
        """Add a highlight, underline, note, text box or ink stroke to a PDF page."""
        if not self.can_annotate:
            raise PermissionDenied("The author of this PDF does not allow comments or highlights to be added.")
        page = self.pages[position]
        updated = replace(page, edits=page.edits + ((len(page.edits), json.dumps(annotation, ensure_ascii=False)),))
        pages = list(self.pages)
        pages[position] = updated
        self._apply(label, tuple(pages), position)

    def clear_annotations(self, position: int) -> None:
        page = self.pages[position]
        if page.edits:
            pages = list(self.pages)
            pages[position] = replace(page, edits=())
            self._apply("Remove annotations", tuple(pages), position)

    # -- undo ------------------------------------------------------------------------------------

    def _apply(self, label: str, pages: tuple[Page, ...], current: int | None = None) -> None:
        self._history.append((label, self.pages))
        self._future.clear()
        self.pages = pages
        self.current = max(0, min(current if current is not None else self.current, len(pages) - 1))
        self.pages_changed.emit()
        self.state_changed.emit()
        self._refresh_timer.start()

    @property
    def undo_label(self) -> str:
        return self._history[-1][0] if self._history else ""

    @property
    def redo_label(self) -> str:
        return self._future[-1][0] if self._future else ""

    def undo(self) -> None:
        if not self._history:
            return
        label, pages = self._history.pop()
        self._future.append((label, self.pages))
        self.pages = pages
        self.current = min(self.current, len(pages) - 1)
        self.pages_changed.emit()
        self.state_changed.emit()
        self._refresh_timer.start()

    def redo(self) -> None:
        if not self._future:
            return
        label, pages = self._future.pop()
        self._history.append((label, self.pages))
        self.pages = pages
        self.current = min(self.current, len(pages) - 1)
        self.pages_changed.emit()
        self.state_changed.emit()
        self._refresh_timer.start()

    # -- page operations ---------------------------------------------------------------------------

    def _require_editable(self) -> None:
        if not self.can_edit_pages:
            raise PermissionDenied(self.edit_block_reason)

    def move(self, positions: list[int], target: int) -> None:
        self._require_editable()
        positions = sorted(set(positions))
        moving = [self.pages[i] for i in positions]
        remaining = [p for i, p in enumerate(self.pages) if i not in set(positions)]
        insert_at = target - sum(1 for i in positions if i < target)
        result = remaining[:insert_at] + moving + remaining[insert_at:]
        if tuple(result) == self.pages:
            return
        self._apply(f"Move {self._count(len(moving))}", tuple(result), insert_at)

    def delete(self, positions: list[int]) -> None:
        self._require_editable()
        doomed = set(positions)
        if len(doomed) >= len(self.pages):
            raise ViewerError(f"A document must keep at least one {self.noun}.")
        kept = tuple(p for i, p in enumerate(self.pages) if i not in doomed)
        self._apply(f"Delete {self._count(len(doomed))}", kept, min(positions))

    def duplicate(self, positions: list[int]) -> None:
        self._require_editable()
        positions = sorted(set(positions))
        pages = list(self.pages)
        copies = [replace(self.pages[i], uid=next(_uids)) for i in positions]
        at = positions[-1] + 1
        pages[at:at] = copies
        self._apply(f"Duplicate {self._count(len(copies))}", tuple(pages), at)

    def insert_pages(self, pages: list[Page], at: int, label: str) -> None:
        self._require_editable()
        fresh = [replace(page, uid=next(_uids)) for page in pages]
        result = list(self.pages)
        result[at:at] = fresh
        self._apply(label, tuple(result), at)

    def add_blank_pdf_page(self, at: int) -> None:
        self._require_editable()
        reference = self.ref_at(max(0, at - 1)) or self.ref_at(0)
        width, height = (reference.width, reference.height) if reference else (612.0, 792.0)
        path = self.workspace.session.new_dir("blank") / "blank.pdf"
        blank_page_pdf(path, width, height)
        page = new_page(str(path), 0)
        self.workspace.renders[render_key(page)] = RenderRef(str(path), 0, None, width, height)
        self.insert_pages([page], at, "Add blank page")

    def add_slide(self, at: int, layout: str | None = None) -> None:
        self._require_editable()
        if self.deck is None:
            raise ViewerError("Slides can be added once this file has been converted to PPTX.")
        if layout is None:
            near = self.pages[max(0, at - 1)] if self.pages else None
            layout = near.layout if near is not None and near.layout else None
            if layout is None and near is not None and 0 <= near.index < len(self.deck.slides) and near.source == self.base_path:
                layout = self.deck.slides[near.index].layout_part
            if layout is None and self.deck.layouts:
                layout = self.deck.layouts[0][0]
        page = new_page(self.base_path, -1, layout=layout)
        self.insert_pages([page], at, "Add slide")

    def text_blocks(self, position: int) -> list[TextBlock]:
        page = self.pages[position]
        return list_text_blocks(page.source, page.spec())

    def edit_text(self, position: int, changes: list[tuple[int, str]]) -> None:
        self._require_editable()
        page = self.pages[position]
        merged = dict(page.edits)
        merged.update(changes)
        updated = replace(page, edits=tuple(sorted(merged.items())))
        pages = list(self.pages)
        pages[position] = updated
        self._apply("Edit text", tuple(pages), position)

    def _count(self, count: int) -> str:
        return f"{count} {self.noun}{'' if count == 1 else 's'}"

    # -- rendering ---------------------------------------------------------------------------------

    @property
    def _deck_size(self) -> tuple[int, int] | None:
        return (self.deck.width_emu, self.deck.height_emu) if self.deck else None

    def _key_at(self, position: int) -> tuple:
        page = self.pages[position]
        return render_key(page, position + 1 if page.numbered else None, self._size_marker(page))

    def _size_marker(self, page: Page) -> tuple[int, int] | None:
        """Slides from a deck of another size look different here, so they need their own render."""
        if self.family != "office" or page.source == self.base_path or self.deck is None:
            return None
        if page.source not in self._source_sizes:
            try:
                other = read_deck_info(page.source)
                same = (other.width_emu, other.height_emu) == self._deck_size
                self._source_sizes[page.source] = None if same else self._deck_size
            except ViewerError:
                self._source_sizes[page.source] = None
        return self._source_sizes[page.source]

    def ref_at(self, position: int) -> RenderRef | None:
        if not 0 <= position < len(self.pages):
            return None
        renders = self.workspace.renders
        ref = renders.get(self._key_at(position))
        if ref is None:
            page = self.pages[position]
            ref = renders.get(render_key(page))
            if ref is None and self.family == "pdf" and page.edits:
                ref = renders.get(render_key(replace(page, edits=())))  # until its annotations are drawn
        return ref

    def page_size(self, position: int) -> tuple[float, float]:
        ref = self.ref_at(position)
        if ref is not None:
            return ref.width, ref.height
        if self.deck is not None:
            return self.deck.width_emu / EMU_PER_POINT, self.deck.height_emu / EMU_PER_POINT
        return 960.0, 540.0

    def start_rendering(self) -> None:
        """Begin showing pages, from the cache when this exact file was rendered before."""
        if self.family != "office":
            self.preparing = False
            return
        self.workspace.run(self._render_all, self._rendering_finished, self._rendering_failed, office=True)

    def _cache_folder(self) -> Path | None:
        if self.protected:
            return None  # decrypted content never goes in the persistent cache
        digest = hashlib.sha256()
        with open(self.base_path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(RENDER_VERSION.encode())
        folder = paths.cache_dir() / digest.hexdigest()[:40]
        folder.mkdir(exist_ok=True)
        return folder

    def _render_all(self) -> dict:
        engine = self.workspace.office
        folder = self._cache_folder()
        cached = self._load_manifest(folder) if folder else None
        if cached is not None:
            self._register_manifest(folder, cached)
            return {"hidden": cached.get("hidden")}
        out = folder or self.workspace.session.new_dir("render")
        pages = self.pages

        if self.format == "ppt":
            pdf = out / "document.pdf"
            reply = engine.to_pdf(Path(self.base_path), pdf)
            manifest = {"version": RENDER_VERSION, "chunks": [{"file": pdf.name, "indexes": list(range(reply["pages"]))}], "hidden": reply["hidden"]}
            if folder:
                (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self._register_chunk(out / pdf.name, list(range(reply["pages"])))
            return {"hidden": reply["hidden"]}

        chunks = []
        first = list(range(min(FIRST_CHUNK, len(pages))))
        groups = [first] if first else []
        for start in range(len(first), len(pages), CHUNK_SIZE):
            groups.append(list(range(start, min(start + CHUNK_SIZE, len(pages)))))
        for number, indexes in enumerate(groups):
            if self.closed:
                return {"hidden": None}
            specs = [SlideSpec(self.base_path, index) for index in indexes]
            pdf = engine.render_specs(self.base_path, specs, out, first_number=indexes[0] + 1, label=f"chunk-{number:04d}")
            self._register_chunk(pdf, indexes)
            chunks.append({"file": pdf.name, "indexes": indexes})
            self.renders_changed.emit()
        if folder:
            (folder / "manifest.json").write_text(json.dumps({"version": RENDER_VERSION, "chunks": chunks}), encoding="utf-8")
        return {"hidden": None}

    @staticmethod
    def _load_manifest(folder: Path) -> dict | None:
        path = folder / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if manifest.get("version") != RENDER_VERSION:
            return None
        if not all((folder / chunk["file"]).exists() for chunk in manifest.get("chunks", [])):
            return None
        return manifest

    def _register_manifest(self, folder: Path, manifest: dict) -> None:
        for chunk in manifest["chunks"]:
            self._register_chunk(folder / chunk["file"], chunk["indexes"])
        paths.cache_dir()
        folder.touch()

    def _register_chunk(self, pdf: Path, indexes: list[int]) -> None:
        sizes = self.workspace.pdfium.page_sizes(str(pdf))
        renders = self.workspace.renders
        for position, index in enumerate(indexes):
            width, height = sizes[position]
            ref = RenderRef(str(pdf), position, None, width, height)
            page = Page(0, self.base_path, index)
            renders[render_key(page)] = ref
            renders[render_key(page, index + 1)] = ref

    def _rendering_finished(self, result: dict) -> None:
        if self.format == "ppt" and not self.pages:
            count = sum(1 for key in self.workspace.renders if key[0] == self.base_path and key[4] is None)
            hidden = result.get("hidden") or [False] * count
            self.pages = tuple(new_page(self.base_path, i, hidden=hidden[i]) for i in range(count))
            self._saved_pages = self.pages
            self._opened_pages = self.pages
            self.pages_changed.emit()
        self.preparing = False
        self.renders_changed.emit()
        self.state_changed.emit()
        paths.trim_cache()

    def _rendering_failed(self, error: ViewerError) -> None:
        self.preparing = False
        self.failed.emit(str(error))
        self.state_changed.emit()

    def _refresh_renders(self) -> None:
        """Render whatever the current state needs and does not have yet."""
        if self.family == "pdf":
            self._refresh_annotated()
            return
        if self.family != "office" or self.preparing or self.format == "ppt":
            return
        missing = [i for i in range(len(self.pages)) if self._key_at(i) not in self.workspace.renders]
        if not missing:
            self.renders_changed.emit()
            return
        runs: list[list[int]] = []
        for position in missing:
            if runs and runs[-1][-1] == position - 1:
                runs[-1].append(position)
            else:
                runs.append([position])
        jobs = [([self.pages[i].spec() for i in run], [self._key_at(i) for i in run], run[0] + 1) for run in runs]
        self.status.emit("Updating preview…")
        self.workspace.run(lambda: self._render_runs(jobs), self._runs_done, self._rendering_failed, office=True)

    def _refresh_annotated(self) -> None:
        renders = self.workspace.renders
        wanted = {render_key(page): page for page in self.pages if page.edits and render_key(page) not in renders}
        if not wanted:
            self.renders_changed.emit()
            return

        def work() -> None:
            from ..core.pdf_annotations import annotated_page
            from .annotation_text import paint_text

            for key, page in wanted.items():
                if self.closed:
                    return
                plain = renders.get(render_key(replace(page, edits=())))
                out = self.workspace.session.new_dir("annotated") / "page.pdf"
                annotated_page(page.source, plain.password if plain else self.password, page.index,
                               [text for _order, text in page.edits], out, paint_text)
                width, height = self.workspace.pdfium.page_sizes(str(out))[0]
                renders[key] = RenderRef(str(out), 0, None, width, height)
                self.renders_changed.emit()

        self.workspace.run(work, lambda _result: self.renders_changed.emit(), self._rendering_failed)

    def _render_runs(self, jobs: list) -> None:
        engine = self.workspace.office
        for specs, keys, first_number in jobs:
            if self.closed:
                return
            folder = self.workspace.session.new_dir("edit")
            pdf = engine.render_specs(self.base_path, specs, folder, first_number=first_number)
            sizes = self.workspace.pdfium.page_sizes(str(pdf))
            for position, key in enumerate(keys):
                width, height = sizes[position]
                self.workspace.renders[key] = RenderRef(str(pdf), position, None, width, height)
            self.renders_changed.emit()

    def _runs_done(self, _result) -> None:
        self.status.emit("")
        self.renders_changed.emit()

    # -- conversion of .ppt ---------------------------------------------------------------------------

    def _converted_pptx(self) -> str:
        """A PPTX version of a .ppt, created once. Runs on the office thread."""
        if self.ppt_as_pptx is None:
            out = self.workspace.session.new_dir("converted") / "converted.pptx"
            self.workspace.office.to_pptx(Path(self.base_path), out)
            self.ppt_as_pptx = str(out)
        return self.ppt_as_pptx

    def convert_for_editing(self, on_done) -> None:
        self.convert_to_pptx(on_done)

    def convert_to_pptx(self, on_done) -> None:
        """Switch a .ppt document to an editable PPTX. The original file is not touched."""
        def work():
            path = self._converted_pptx()
            return path, read_deck_info(path), inspect.inspect_pptx(path)

        def finished(result):
            path, deck, notes = result
            self.base_path = path
            self.deck = deck
            self.format = "pptx"
            self.converted = True
            self.notes = notes + [inspect.Note("Converted for editing", "This .ppt was converted to PPTX for editing. Save As creates a new PPTX and your original stays unchanged.", False)]
            self.pages = tuple(new_page(path, s.index, hidden=s.hidden, numbered=s.has_slide_number) for s in deck.slides)
            self._saved_pages = self.pages
            self._history.clear()
            self._future.clear()
            self.preparing = True
            self.pages_changed.emit()
            self.state_changed.emit()
            self.start_rendering()
            on_done()

        self.workspace.run(work, finished, lambda error: self.failed.emit(str(error)), office=True)

    # -- saving ---------------------------------------------------------------------------------------

    def _pdf_specs(self, pages: tuple[Page, ...] | list[Page]) -> list[PdfPageSpec]:
        specs = []
        for page in pages:
            ref = self.workspace.renders.get(render_key(replace(page, edits=())))
            annotations = tuple(text for _order, text in page.edits)
            specs.append(PdfPageSpec(page.source, ref.password if ref else self.password, page.index, annotations))
        return specs

    @staticmethod
    def _paint():
        from .annotation_text import paint_text

        return paint_text

    def save_copy(self, out: Path, *, unprotected: bool = False) -> list[str]:
        """Write the current state to `out`. Runs on a worker thread. Returns warnings."""
        out = Path(out)
        if self.family == "pdf":
            return self._save_pdf(out, unprotected)
        return self._save_office(out, unprotected)

    def _save_pdf(self, out: Path, unprotected: bool) -> list[str]:
        if unprotected and not self.access.may_remove_protection:
            raise PermissionDenied(
                "This PDF restricts editing or copying. Only the author's owner password can authorize an unprotected copy."
            )
        if self.pristine:
            if unprotected:
                remove_protection(self.raw_copy, self.password, self.access, out)
            else:
                copy_file(self.raw_copy, out)
            return []
        base = PdfPageSpec(self.base_path, self.password, 0)
        encryption = bool(self.protected and not unprotected)
        write_pdf(base, self._pdf_specs(self.pages), out, encryption=encryption, paint=self._paint())
        return []

    def _save_office(self, out: Path, unprotected: bool) -> list[str]:
        keep_password = self.protected and not unprotected and self.password
        if self.format == "ppt":
            if out.suffix.lower() == ".ppt":
                copy_file(self.base_path if unprotected else self.raw_copy, out)
                return []
            pptx = self._converted_pptx()
            if keep_password:
                encrypt_pptx(pptx, self.password, out)
            else:
                copy_file(pptx, out)
            return ["This .ppt was converted to PPTX, so some formatting may differ from the original."]

        if self.pristine and out.suffix.lower() == Path(self.raw_copy).suffix.lower():
            copy_file(self.base_path if unprotected or not self.protected else self.raw_copy, out)
            return []

        working = self.workspace.session.new_dir("save") / "output.pptx"
        report = build_deck(self.base_path, [page.spec() for page in self.pages], working)
        if keep_password:
            encrypt_pptx(working, self.password, out)
        else:
            copy_file(working, out)
        working.unlink(missing_ok=True)
        return report.warnings

    def mark_saved(self) -> None:
        self._saved_pages = self.pages
        self._saved_format = self.format
        self.state_changed.emit()

    def export_positions(self, positions: list[int], out: Path) -> list[str]:
        """Write a subset of pages to a new file, for Split. Runs on a worker thread."""
        pages = [self.pages[i] for i in positions]
        if self.family == "pdf":
            base = PdfPageSpec(self.base_path, self.password, 0)
            write_pdf(base, self._pdf_specs(pages), out, encryption=bool(self.protected), paint=self._paint())
            return []
        base_path = self.base_path
        specs = [page.spec() for page in pages]
        if self.format == "ppt":
            base_path = self._converted_pptx()
            specs = [SlideSpec(base_path, page.index) for page in pages]
        working = self.workspace.session.new_dir("split") / "output.pptx"
        report = build_deck(base_path, specs, working)
        if self.protected and self.password:
            encrypt_pptx(working, self.password, out)
        else:
            copy_file(working, out)
        working.unlink(missing_ok=True)
        return report.warnings

    # -- fonts ----------------------------------------------------------------------------------------

    def add_font_notes(self) -> None:
        if self.format != "pptx":
            return
        installed = set(QFontDatabase.families())
        try:
            self.notes.extend(inspect.font_notes(self.base_path, installed))
        except ViewerError:
            pass

    def close(self) -> None:
        self.closed = True
        self._refresh_timer.stop()


def merge_files(workspace: Workspace, inputs: list[tuple[Path, str | None]], out: Path) -> list[str]:
    """Merge documents of one family into a new file. Runs on the office thread when needed."""
    kinds = [sniff(path) for path, _password in inputs]
    families = {kind.family for kind in kinds}
    if len(families) != 1:
        raise ViewerError("PDF files and PowerPoint files cannot be merged together.")
    folder = workspace.session.new_dir("merge")

    if families == {"pdf"}:
        from ..core.pdf_engine import merge_pdfs

        prepared = []
        for path, password in inputs:
            access = read_access(path, password)
            if not access.can_assemble:
                raise PermissionDenied(f"The author of {path.name} does not allow its pages to be copied into another file.")
            prepared.append((str(path), password))
        merge_pdfs(prepared, [path.stem for path, _password in inputs], out)
        return []

    decks: list[str] = []
    for number, ((path, password), kind) in enumerate(zip(inputs, kinds)):
        local = path
        if kind.encrypted:
            if not password:
                raise PasswordRequired(f"{path.name} is protected with a password.")
            if kind.format == "ppt":
                local = folder / f"unlocked-{number}.ppt"
                decrypt_ppt(path, password, local)
            else:
                local = folder / f"unlocked-{number}.pptx"
                decrypt_office(path, password, local)
        if kind.format == "ppt":
            converted = folder / f"converted-{number}.pptx"
            workspace.office.to_pptx(Path(local), converted)
            local = converted
        decks.append(str(local))

    specs = []
    for deck in decks:
        info = read_deck_info(deck)
        if info.modify_protected:
            raise PermissionDenied(f"The author of {Path(deck).name} marked it read-only with a modify password.")
        specs.extend(SlideSpec(deck, slide.index) for slide in info.slides)
    working = folder / "merged.pptx"
    report = build_deck(decks[0], specs, working)
    copy_file(working, out)
    return report.warnings
