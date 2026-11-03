"""Turn a Markdown file into styled HTML plus a list of images and formulas.

The output is a template, because every size in it depends on the zoom level. Nothing here
writes to the Markdown file or to its folder.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote

from . import paths
from .errors import CorruptFile
from .inspect import Note

CACHE_VERSION = "2"

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTRIBUTE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_ZOOM = re.compile(r"zoom\s*:\s*([\d.]+)\s*(%?)", re.IGNORECASE)
_VIEW_BOX = re.compile(r'viewBox="[-\d.]+ ([-\d.]+) ([\d.]+) ([\d.]+)"')


@dataclass
class Asset:
    key: int
    kind: str  # image or math
    source: str  # a file path, a data URI, or LaTeX
    display: bool = False
    zoom: float = 1.0  # the document's own scale, such as style="zoom:40%"
    width: float = 0.0  # natural size in logical pixels at 100%
    height: float = 0.0
    blob: bytes | None = None  # SVG bytes for formulas, decoded data for data URIs
    error: str = ""
    alt: str = ""
    pad_top: float = 0.0  # blank space that aligns an inline formula with its line of text
    pad_bottom: float = 0.0
    depth: int = 0  # how many quotes and lists surround it, each of which narrows the column


@dataclass
class Heading:
    level: int
    text: str
    anchor: str


@dataclass
class MarkdownPage:
    path: Path
    template: str
    assets: list[Asset]
    headings: list[Heading]
    notes: list[Note] = field(default_factory=list)


def read_text(path: Path) -> str:
    return _decode(_read_bytes(path))


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CorruptFile(f"{path.name} could not be read ({exc}).") from exc


def _decode(data: bytes) -> str:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _mark_rule(state, silent: bool) -> bool:
    """Typora's ==highlight== syntax."""
    if state.src[state.pos : state.pos + 2] != "==":
        return False
    close = state.src.find("==", state.pos + 2, state.posMax)
    if close < 0 or close == state.pos + 2 or state.src[state.pos + 2].isspace():
        return False
    if not silent:
        state.push("mark_open", "span", 1)
        start, stop = state.pos, state.posMax
        state.pos, state.posMax = start + 2, close
        state.md.inline.tokenize(state)
        state.posMax = stop
        state.push("mark_close", "span", -1)
    state.pos = close + 2
    return True


def _relax_asterisk_rules() -> None:
    """Read `**中文。**继续` and `到**(0, 1)**之间` as bold, as Typora does.

    CommonMark's flanking rules were written for spaced languages and leave the asterisks
    showing in Chinese text. Here an asterisk run opens unless a space follows it, and closes
    unless a space precedes it, which is what Typora's readers expect.
    """
    from markdown_it.rules_inline.state_inline import Scanned, StateInline

    if getattr(StateInline, "_james_patched", False):
        return
    original = StateInline.scanDelims

    def scan(self, start, canSplitWord):
        result = original(self, start, canSplitWord)
        if self.src[start] != "*":
            return result
        before = self.src[start - 1] if start > 0 else " "
        position = start + result.length
        after = self.src[position] if position < self.posMax else " "
        return Scanned(not after.isspace(), not before.isspace(), result.length)

    StateInline.scanDelims = scan
    StateInline._james_patched = True


class _Builder:
    def __init__(self, path: Path):
        self.path = path
        self.base = path.parent
        self.assets: list[Asset] = []
        self.headings: list[Heading] = []
        self.remote_images = 0
        self.missing_images: list[str] = []
        self.depth = 0

    def add(self, asset: Asset) -> Asset:
        asset.depth = self.depth
        self.assets.append(asset)
        return asset

    def image(self, source: str, alt: str, zoom: float = 1.0, width: float = 0, height: float = 0) -> str:
        source = html.unescape(source).strip()
        if source.lower().startswith(("http://", "https://", "//")):
            self.remote_images += 1
            return _missing_box(alt or source, "online image, not downloaded")
        if source.lower().startswith("data:image"):
            try:
                blob = base64.b64decode(source.split(",", 1)[1])
            except (ValueError, IndexError):
                return _missing_box(alt, "damaged embedded image")
            asset = self.add(Asset(len(self.assets), "image", source[:40], zoom=zoom, width=width, height=height, blob=blob, alt=alt))
            return f"⟦{asset.key}⟧"
        candidate = Path(unquote(source.split("#")[0].split("?")[0].replace("file:///", "")))
        if not candidate.is_absolute():
            candidate = self.base / candidate
        if not candidate.is_file():
            self.missing_images.append(source)
            return _missing_box(alt or Path(source).name, "image file not found")
        asset = self.add(Asset(len(self.assets), "image", str(candidate), zoom=zoom, width=width, height=height, alt=alt))
        return f"⟦{asset.key}⟧"

    def rewrite_img_tags(self, content: str) -> str:
        def replace(match: re.Match) -> str:
            attributes = {
                name.lower(): (first or second or third or "")
                for name, first, second, third in _ATTRIBUTE.findall(match.group(0))
            }
            zoom = 1.0
            found = _ZOOM.search(attributes.get("style", ""))
            if found:
                zoom = float(found.group(1)) / (100 if found.group(2) else 1)
            return self.image(
                attributes.get("src", ""),
                attributes.get("alt", ""),
                zoom,
                _pixels(attributes.get("width")),
                _pixels(attributes.get("height")),
            )

        return _IMG_TAG.sub(replace, content)

    def math(self, latex: str, display: bool) -> str:
        asset = self.add(Asset(len(self.assets), "math", latex.strip(), display=display))
        return f"⟦{asset.key}⟧"


def _pixels(value: str | None) -> float:
    if not value:
        return 0
    found = re.fullmatch(r"\s*([\d.]+)\s*(px)?\s*", value)
    return float(found.group(1)) if found else 0


def _missing_box(label: str, reason: str) -> str:
    return (
        f'<span style="color:#9a6a3c; background-color:#fbf4ea;">&nbsp;[{html.escape(label)}: {reason}]&nbsp;</span>'
    )


def _highlight(code: str, language: str) -> str:
    """Colour code with Pygments. Unknown languages stay plain."""
    code = code.expandtabs(4)  # Qt's tab stops are far wider than any code editor's
    if language:
        try:
            from pygments import highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import get_lexer_by_name
            from pygments.util import ClassNotFound

            try:
                lexer = get_lexer_by_name(language.lower())
            except ClassNotFound:
                return html.escape(code)
            coloured = highlight(code, lexer, HtmlFormatter(nowrap=True, noclasses=True, style="friendly", nobackground=True))
            return coloured.replace("font-style: italic", "font-style: normal")  # slanted Chinese reads badly
        except ImportError:
            pass
    return html.escape(code)


def _make_parser(builder: _Builder):
    from markdown_it import MarkdownIt
    from mdit_py_plugins.dollarmath import dollarmath_plugin
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.front_matter import front_matter_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin

    _relax_asterisk_rules()
    parser = (
        MarkdownIt("commonmark", {"html": True, "breaks": False})
        .enable("table")
        .enable("strikethrough")
        .use(dollarmath_plugin, allow_space=True, allow_digits=True, double_inline=True)
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin, enabled=False)
    )
    parser.inline.ruler.before("emphasis", "mark", _mark_rule)
    rules = parser.renderer.rules

    def heading_open(tokens, index, options, env):
        token = tokens[index]
        level = int(token.tag[1])
        text = "".join(child.content for child in (tokens[index + 1].children or []) if child.type in ("text", "code_inline", "math_inline"))
        anchor = f"h{len(builder.headings)}"
        builder.headings.append(Heading(level, text.strip() or f"Heading {len(builder.headings) + 1}", anchor))
        # A styled paragraph, because Qt overrides the font size of real heading tags.
        return f'<p style="%%H{level}%%"><a name="{anchor}"></a>'

    def heading_close(tokens, index, options, env):
        return "</p>\n"

    def fence(tokens, index, options, env):
        token = tokens[index]
        language = (token.info or "").strip().split(" ")[0]
        code = token.content.rstrip("\n")
        return (
            '<table class="code" width="100%" cellspacing="0" cellpadding="%%CODEPAD%%" bgcolor="#f4f7f5"><tr><td>'
            f'<pre class="code">{_highlight(code, language)}</pre></td></tr></table><p class="gap"></p>\n'
        )

    def code_inline(tokens, index, options, env):
        return f'<span class="inline-code">&nbsp;{html.escape(tokens[index].content)}&nbsp;</span>'

    def table_open(tokens, index, options, env):
        return '<table class="data" cellspacing="0" cellpadding="%%TABLEPAD%%" width="100%">'

    def table_close(tokens, index, options, env):
        return '</table><p class="gap"></p>\n'

    def cell(tag: str):
        def render(tokens, index, options, env):
            style = tokens[index].attrGet("style") or ""
            align = "center" if "center" in style else "right" if "right" in style else "left"
            extra = ' bgcolor="#eef5f0"' if tag == "th" else ""
            return f'<{tag} align="{align}"{extra} style="border-bottom:1px solid #dfe8e2;">'

        return render

    def nesting(change: int):
        """Track how deep the renderer is, then render the list tag the normal way."""

        def render(tokens, index, options, env):
            builder.depth += change
            return parser.renderer.renderToken(tokens, index, options, env)

        return render

    def blockquote_open(tokens, index, options, env):
        builder.depth += 1
        return '<table class="quote" width="100%" cellspacing="0" cellpadding="0"><tr><td width="3" bgcolor="#6fb186"></td><td bgcolor="#f6faf7" style="padding:%%QUOTEPAD%%px;">'

    def blockquote_close(tokens, index, options, env):
        builder.depth -= 1
        return '</td></tr></table><p class="gap"></p>\n'

    def image(tokens, index, options, env):
        token = tokens[index]
        alt = "".join(child.content for child in (token.children or []))
        return builder.image(token.attrGet("src") or "", alt)

    def html_any(tokens, index, options, env):
        return builder.rewrite_img_tags(tokens[index].content)

    def html_block(tokens, index, options, env):
        content = tokens[index].content
        if _IMG_TAG.search(content) and not _IMG_TAG.sub("", content).strip():
            return f'<p align="center">{builder.rewrite_img_tags(content)}</p>\n'  # centred, like Typora
        return builder.rewrite_img_tags(content)

    def paragraph_open(tokens, index, options, env):
        if tokens[index].hidden:
            return ""
        children = tokens[index + 1].children or []
        only_images = children and all(
            child.type == "image" or (child.type == "html_inline" and _IMG_TAG.fullmatch(child.content.strip()))
            or (child.type in ("softbreak", "text") and not child.content.strip())
            for child in children
        )
        return '<p align="center">' if only_images else "<p>"

    def math_inline(tokens, index, options, env):
        return builder.math(tokens[index].content, False)

    def math_block(tokens, index, options, env):
        return f'<p align="center">{builder.math(tokens[index].content, True)}</p>\n'

    def mark_open(tokens, index, options, env):
        return '<span style="background-color:#fdf3c4;">'

    def mark_close(tokens, index, options, env):
        return "</span>"

    rules.update(
        heading_open=heading_open,
        heading_close=heading_close,
        fence=fence,
        code_block=fence,
        code_inline=code_inline,
        table_open=table_open,
        table_close=table_close,
        th_open=cell("th"),
        td_open=cell("td"),
        blockquote_open=blockquote_open,
        blockquote_close=blockquote_close,
        bullet_list_open=nesting(1),
        bullet_list_close=nesting(-1),
        ordered_list_open=nesting(1),
        ordered_list_close=nesting(-1),
        image=image,
        paragraph_open=paragraph_open,
        html_block=html_block,
        html_inline=html_any,
        math_inline=math_inline,
        math_inline_double=math_inline,  # $$x$$ inside a sentence stays in the sentence, as in Typora
        math_block=math_block,
        math_block_label=math_block,
        mark_open=mark_open,
        mark_close=mark_close,
    )
    return parser


def _render_formula(asset: Asset, font_px: float = 16.0) -> None:
    """Draw one formula to SVG. A formula that cannot be drawn is shown as its source text."""
    import ziafont
    import ziamath

    # QtSvg cannot draw the <symbol> elements of the default SVG 2 output, so glyphs would go missing.
    ziamath.config.svg2 = False
    ziafont.config.svg2 = False
    try:
        formula = ziamath.Latex(asset.source.replace("​", ""), size=font_px, inline=not asset.display)
        svg = formula.svg()
        asset.blob = svg.encode("utf-8") if isinstance(svg, str) else svg
        found = _VIEW_BOX.search(svg if isinstance(svg, str) else svg.decode("utf-8"))
        top, asset.width, asset.height = float(found.group(1)), float(found.group(2)), float(found.group(3))
        if not asset.display:
            _centre_on_text(asset, ascent=-top, descent=asset.height + top, font_px=font_px)
    except Exception as exc:  # the renderer raises assorted errors on unusual LaTeX
        asset.error = str(exc) or type(exc).__name__


def _centre_on_text(asset: Asset, ascent: float, descent: float, font_px: float) -> None:
    """Pad an inline formula so its baseline lands on the text's baseline.

    Qt can only centre an inline image, which it does a fixed distance above the text baseline
    (measured from screenshots as about 0.29 em). Padding the shorter side moves the formula's
    own baseline down to where the text's baseline is.
    """
    centre = font_px * 0.29
    above, below = ascent - centre, descent + centre
    asset.pad_top = max(0.0, below - above)
    asset.pad_bottom = max(0.0, above - below)


def parse_markdown(path: Path) -> MarkdownPage:
    """Runs on a worker thread. The Markdown file and its folder are only ever read.

    Reopening a file is served from a saved result, which skips parsing and redrawing every
    formula. The saved result is ignored as soon as the file or any image it uses has changed.
    """
    path = Path(path)
    data = _read_bytes(path)
    key = hashlib.sha256(CACHE_VERSION.encode() + str(path.parent).encode("utf-8", "replace") + data).hexdigest()[:40]
    cached = _load_saved(path, key)
    if cached is not None:
        return cached
    page, missing = _parse(path, _decode(data))
    _save(page, key, missing)
    return page


def _image_stamp(asset: Asset) -> list:
    if asset.kind != "image" or asset.blob is not None:
        return [asset.key, 0, 0]
    stat = Path(asset.source).stat()
    return [asset.key, stat.st_size, stat.st_mtime_ns]


def _saved_path(key: str) -> Path:
    return paths.cache_dir() / "markdown" / f"{key}.json"


def _save(page: MarkdownPage, key: str, missing: list[str]) -> None:
    """Saving is only a speed-up, so failing to save must never stop a document from opening."""
    try:
        assets = []
        for asset in page.assets:
            values = asdict(asset)
            values["blob"] = base64.b64encode(asset.blob).decode("ascii") if asset.blob is not None else None
            assets.append(values)
        record = {
            "template": page.template,
            "assets": assets,
            "stamps": [_image_stamp(asset) for asset in page.assets],
            "missing": missing,
            "headings": [asdict(heading) for heading in page.headings],
            "notes": [asdict(note) for note in page.notes],
        }
        target = _saved_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    except (OSError, TypeError, ValueError):
        pass


def _load_saved(path: Path, key: str) -> MarkdownPage | None:
    try:
        record = json.loads(_saved_path(key).read_text(encoding="utf-8"))
        assets = []
        for values in record["assets"]:
            blob = values.pop("blob")
            assets.append(Asset(**values, blob=base64.b64decode(blob) if blob is not None else None))
        if [_image_stamp(asset) for asset in assets] != record["stamps"]:
            return None  # an image was edited or removed
        if any((path.parent / unquote(source)).is_file() for source in record["missing"]):
            return None  # a missing image has appeared
        return MarkdownPage(
            path,
            record["template"],
            assets,
            [Heading(**heading) for heading in record["headings"]],
            [Note(**note) for note in record["notes"]],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _parse(path: Path, text: str) -> tuple[MarkdownPage, list[str]]:
    builder = _Builder(path)
    parser = _make_parser(builder)
    body = parser.render(text)
    for asset in builder.assets:
        if asset.kind == "math":
            _render_formula(asset)

    notes: list[Note] = []
    failed = [asset for asset in builder.assets if asset.kind == "math" and asset.error]
    if failed:
        notes.append(Note("Some formulas could not be drawn", f"{len(failed)} formula(s) use LaTeX the formula renderer does not support, so their source text is shown instead.", True))
    if builder.remote_images:
        notes.append(Note("Online images are not downloaded", f"{builder.remote_images} image(s) are on the internet. James Viewer works offline, so a label is shown instead.", True))
    if builder.missing_images:
        notes.append(Note("Some images were not found", f"{len(builder.missing_images)} image file(s) are missing, for example {builder.missing_images[0]}.", True))
    if "<script" in body.lower():
        notes.append(Note("Scripts are not run", "This file contains HTML scripts. James Viewer never runs them.", False))
    notes.append(Note("HTML support is basic", "Plain Markdown, tables, code, images and formulas are supported. Advanced HTML and CSS inside the file may look simpler than in a web browser.", False))
    return MarkdownPage(path, body, builder.assets, builder.headings, notes), builder.missing_images
