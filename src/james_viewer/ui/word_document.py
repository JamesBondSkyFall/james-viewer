"""An open Word document. The office engine lays out its pages, and text is edited paragraph by paragraph.

Every page carries the document's whole set of text edits, so undo restores an earlier set the same way
it restores earlier pages elsewhere. A change can move text onto another page, so after each render the
number of pages follows the new layout.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from ..core import paths
from ..core.docx_engine import Paragraph, read_paragraphs, read_restricted, write_word
from ..core.errors import PermissionDenied, ViewerError
from ..core.pdf_engine import PDFIUM_LOCK, copy_file
from ..core.pptx_engine import TextBlock
from ..core.security import encrypt_pptx
from .document import RENDER_VERSION, Document, Page, new_page, render_key
from .services import RenderRef

_PLACES = {"body": "Paragraph", "table": "Table cell", "text box": "Text box"}
_SPACE = re.compile(r"\s+")


def _squeeze(text: str) -> str:
    return _SPACE.sub("", text.replace("\u2028", ""))


def _fonts(layout: str | None) -> tuple[bool, tuple[int, ...]]:
    """Times New Roman settings, kept in the page's layout field: "all|3,7" or "|3,7"."""
    if not layout:
        return False, ()
    everywhere, _, indexes = layout.partition("|")
    return everywhere == "all", tuple(int(index) for index in indexes.split(",") if index)


def _font_layout(everywhere: bool, indexes) -> str | None:
    indexes = sorted(set(indexes))
    if not everywhere and not indexes:
        return None
    return ("all" if everywhere else "") + "|" + ",".join(map(str, indexes))


class WordDocument(Document):
    def __init__(self, workspace, opened, parent=None):
        super().__init__(workspace, opened, parent)
        self.preparing = True
        self._original: list[Paragraph] | None = None
        self._page_of: dict[tuple, dict[int, int]] = {}
        self._last_layout: dict[int, int] = {}
        self.restricted = self.format == "docx" and read_restricted(self.base_path)

    # -- descriptions ---------------------------------------------------------------------------

    @property
    def noun(self) -> str:
        return "page"

    @property
    def can_edit_pages(self) -> bool:
        return False

    @property
    def can_edit_text(self) -> bool:
        return not self.restricted

    @property
    def edit_block_reason(self) -> str:
        if self.restricted:
            return "The author restricted editing of this document."
        return "Word pages follow from the text, so they cannot be moved, copied or deleted. Use Edit text to change the words."

    @property
    def edits(self) -> tuple[tuple[int, str], ...]:
        return self.pages[0].edits if self.pages else ()

    @property
    def font_state(self) -> str | None:
        return self.pages[0].layout if self.pages else None

    @property
    def font_everywhere(self) -> bool:
        return _fonts(self.font_state)[0]

    # -- text ---------------------------------------------------------------------------------------

    def _paragraphs(self) -> list[Paragraph]:
        if self._original is None:
            self._original = read_paragraphs(self.base_path)
        current = dict(self.edits)
        return [replace(p, text=current.get(p.index, p.text)) for p in self._original]

    def _layout(self) -> dict[int, int]:
        """Which page each paragraph starts on, found by reading the rendered pages in order."""
        edits = (self.edits, self.font_state)
        if edits in self._page_of:
            return self._page_of[edits]
        refs = [self.ref_at(i) for i in range(len(self.pages))]
        if not refs or any(ref is None for ref in refs):
            return self._last_layout  # the new layout is still being rendered
        texts = []
        for ref in refs:
            with PDFIUM_LOCK:
                page, text = self.workspace.pdfium.text_page(ref.pdf, ref.password, ref.page)
                try:
                    texts.append(_squeeze(text.get_text_range()))
                finally:
                    text.close()
                    page.close()
        starts = []
        total = 0
        for text in texts:
            starts.append(total)
            total += len(text)
        whole = "".join(texts)
        layout: dict[int, int] = {}
        cursor = 0
        for paragraph in self._paragraphs():
            key = _squeeze(paragraph.text)[:40]
            if not key:
                continue
            found = whole.find(key, cursor)
            if found < 0:
                continue
            cursor = found + len(key)
            layout[paragraph.index] = max(i for i, start in enumerate(starts) if start <= found)
        self._page_of[edits] = layout
        self._last_layout = layout
        return layout

    def text_blocks(self, position: int) -> list[TextBlock]:
        layout = self._layout()
        return [
            TextBlock(p.index, f"{_PLACES[p.where]} {p.index + 1}", p.text)
            for p in self._paragraphs()
            if p.editable and layout.get(p.index) == position
        ]

    def edit_text(self, position: int, changes: list[tuple[int, str]], latin_font: bool = False) -> None:
        if not self.can_edit_text:
            raise PermissionDenied(self.edit_block_reason)
        original = {p.index: p.text for p in (self._original or read_paragraphs(self.base_path))}
        merged = dict(self.edits)
        merged.update(changes)
        edits = tuple(sorted((index, text) for index, text in merged.items() if original.get(index) != text))
        everywhere, indexes = _fonts(self.font_state)
        if latin_font:
            indexes = indexes + tuple(index for index, _text in changes)
        layout = _font_layout(everywhere, indexes)
        if edits == self.edits and layout == self.font_state:
            return
        self._apply("Edit text", tuple(replace(page, edits=edits, layout=layout) for page in self.pages), position)

    def set_font_everywhere(self, on: bool) -> None:
        if not self.can_edit_text:
            raise PermissionDenied(self.edit_block_reason)
        _everywhere, indexes = _fonts(self.font_state)
        layout = _font_layout(on, indexes)
        if layout == self.font_state:
            return
        label = "Times New Roman for English text" if on else "Original fonts for English text"
        self._apply(label, tuple(replace(page, layout=layout) for page in self.pages))

    def _write(self, out: Path) -> None:
        everywhere, indexes = _fonts(self.font_state)
        write_word(self.base_path, dict(self.edits), out, font_paragraphs=indexes, font_everywhere=everywhere)

    # -- rendering ------------------------------------------------------------------------------------

    def start_rendering(self) -> None:
        self.workspace.run(self._render_all, self._rendering_finished, self._rendering_failed, office=True)

    def _render_all(self) -> dict:
        folder = self._cache_folder()
        cached = self._load_manifest(folder) if folder else None
        if cached is not None:
            self._register_manifest(folder, cached)
            return {}
        out = folder or self.workspace.session.new_dir("render")
        pdf = out / "document.pdf"
        self.workspace.office.word_to_pdf(Path(self.base_path), pdf)
        indexes = list(range(len(self.workspace.pdfium.page_sizes(str(pdf)))))
        self._register_chunk(pdf, indexes)
        if folder:
            manifest = {"version": RENDER_VERSION, "chunks": [{"file": pdf.name, "indexes": indexes}]}
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return {}

    def _rendering_finished(self, _result) -> None:
        if not self.pages:
            count = sum(1 for key in self.workspace.renders if key[0] == self.base_path and key[2] is None and key[3] == () and key[4] is None)
            self.pages = tuple(new_page(self.base_path, index) for index in range(count))
            self._opened_pages = self.pages
            if not self.converted:
                self._saved_pages = self.pages
            self.pages_changed.emit()
        self.preparing = False
        self.renders_changed.emit()
        self.state_changed.emit()
        paths.trim_cache()

    def _refresh_renders(self) -> None:
        if self.preparing or not self.pages:
            return
        if all(self._key_at(i) in self.workspace.renders for i in range(len(self.pages))):
            self.renders_changed.emit()
            return
        state = (self.edits, self.font_state)
        self.status.emit("Updating preview…")
        self.workspace.run(lambda: self._render_edits(state), self._edits_rendered, self._rendering_failed, office=True)

    def _render_edits(self, state: tuple) -> tuple[tuple, int]:
        edits, layout = state
        folder = self.workspace.session.new_dir("edit")
        docx = folder / "edited.docx"
        everywhere, indexes = _fonts(layout)
        write_word(self.base_path, dict(edits), docx, font_paragraphs=indexes, font_everywhere=everywhere)
        pdf = folder / "edited.pdf"
        self.workspace.office.word_to_pdf(docx, pdf)
        sizes = self.workspace.pdfium.page_sizes(str(pdf))
        for index, (width, height) in enumerate(sizes):
            key = render_key(Page(0, self.base_path, index, layout=layout, edits=edits))
            self.workspace.renders[key] = RenderRef(str(pdf), index, None, width, height)
        return state, len(sizes)

    def _edits_rendered(self, result: tuple[tuple, int]) -> None:
        (edits, layout), count = result
        if (self.edits, self.font_state) == (edits, layout) and len(self.pages) != count:
            self.pages = tuple(new_page(self.base_path, index, layout=layout, edits=edits) for index in range(count))
            self.current = min(self.current, count - 1)
            self.pages_changed.emit()
        self.status.emit("")
        self.renders_changed.emit()
        self.state_changed.emit()

    # -- .doc -----------------------------------------------------------------------------------------

    def convert_for_editing(self, on_done) -> None:
        """Switch a .doc to an editable DOCX. The original file is not touched."""

        def work() -> str:
            out = self.workspace.session.new_dir("converted") / "converted.docx"
            self.workspace.office.word_to_docx(Path(self.base_path), out)
            return str(out)

        def finished(path: str) -> None:
            from ..core import inspect

            self.base_path = path
            self.format = "docx"
            self.converted = True
            self._original = None
            self._page_of.clear()
            self._last_layout = {}
            self.restricted = read_restricted(path)
            self.notes.append(inspect.Note("Converted for editing", "This .doc was converted to DOCX for editing. Save As creates a new DOCX and your original stays unchanged.", False))
            self.pages = ()
            self._history.clear()
            self._future.clear()
            self.preparing = True
            self.pages_changed.emit()
            self.state_changed.emit()
            self.start_rendering()
            on_done()

        self.workspace.run(work, finished, lambda error: self.failed.emit(str(error)), office=True)

    convert_to_pptx = convert_for_editing  # the name the slide views call

    # -- saving ---------------------------------------------------------------------------------------

    def save_copy(self, out: Path, *, unprotected: bool = False) -> list[str]:
        out = Path(out)
        suffix = out.suffix.lower()
        keep_password = self.protected and not unprotected and self.password
        if suffix == ".pdf":
            ref = self.ref_at(0)
            if ref is None or any(self.ref_at(i) is None for i in range(len(self.pages))):
                raise ViewerError("The preview is still being updated. Try again in a moment.")
            copy_file(ref.pdf, out)
            return []
        if self.format == "doc":
            if suffix == ".doc":
                copy_file(self.base_path if unprotected or not self.protected else self.raw_copy, out)
                return []
            working = self.workspace.session.new_dir("save") / "converted.docx"
            self.workspace.office.word_to_docx(Path(self.base_path), working)
            warnings = ["This .doc was converted to DOCX, so some formatting may differ from the original."]
        else:
            if self.pristine and suffix == Path(self.raw_copy).suffix.lower():
                copy_file(self.base_path if unprotected or not self.protected else self.raw_copy, out)
                return []
            working = self.workspace.session.new_dir("save") / "output.docx"
            self._write(working)
            warnings = ["This .doc was converted to DOCX, so some formatting may differ from the original."] if self.converted else []
        if keep_password:
            encrypt_pptx(working, self.password, out)
        else:
            copy_file(working, out)
        working.unlink(missing_ok=True)
        return warnings

    def export_positions(self, positions: list[int], out: Path) -> list[str]:
        raise ViewerError("Word documents cannot be split here. Only PDF and PowerPoint files can.")

    def add_font_notes(self) -> None:
        pass
