"""Markdown viewing, with zoom tested most heavily because it matters most here."""

import shutil
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from conftest import pump, sha256
from james_viewer.core.markdown_engine import parse_markdown, read_text
from james_viewer.core.sniff import sniff
from james_viewer.ui.markdown_view import MAX_ZOOM, MIN_ZOOM, MarkdownPresenter, MarkdownReader, MarkdownTab

REAL_FILE = Path("X:/Machine Learning/day05_聚类算法&pytorch框架/02-笔记/01-PyTorch框架/01-PyTorch框架使用.md")

SOURCE = r"""# 标题 Title

## 第二级 Second

**同一层的神经元学习不同特征。**每个神经元都不同。映射到**(0, 1)**之间，输入**<-6或者>6**时。

这是==高亮 highlighted==文字，公式 $x_i$ 和 $\frac{1}{1+e^{-x}}$ 在行内。

$$
\sum_{i=1}^{n} w_i x_i + b
$$

<img src='assets/dot.png' style="zoom:50%;" />

![plain](assets/dot.png)

![gone](assets/not-here.png)

![remote](https://example.com/picture.png)

| 名称 | Value |
| ---- | ----: |
| 甲   | 1     |

> 引用 quote

- list 列表
  - nested 嵌套

```python
def run():
	return 1  # tab indented
```

### 第三级 Third

""" + "\n\n".join(f"段落 paragraph {number} " + "内容 words " * 40 for number in range(60)) + "\n\n### 末尾 End\n\nThe end.\n"


@pytest.fixture
def notes(work) -> Path:
    (work / "assets").mkdir()
    Image.new("RGB", (400, 200), (92, 157, 114)).save(work / "assets" / "dot.png")
    path = work / "notes.md"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def _reader(view) -> MarkdownReader:
    pump(0.3)
    assert isinstance(view, MarkdownTab)
    reader = view.canvas
    assert pump(5, lambda: reader._rendered_width > 0 and not reader._timer.isActive())
    return reader


def _settle(reader) -> None:
    assert pump(5, lambda: not reader._timer.isActive())
    pump(0.05)


def _wheel(widget, notches: float, modifiers=Qt.KeyboardModifier.NoModifier, point: QPoint | None = None) -> None:
    """Send the same event Windows delivers for a real wheel turn."""
    position = QPointF(point if point is not None else widget.rect().center())
    event = QWheelEvent(
        position, QPointF(widget.mapToGlobal(position.toPoint())), QPoint(0, 0), QPoint(0, int(120 * notches)),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(widget, event)


# -- parsing ------------------------------------------------------------------------------------


def test_chinese_bold_and_highlight_show_no_stray_marks(notes):
    page = parse_markdown(notes)
    assert "**" not in page.template and "==" not in page.template.replace("===", "")
    assert page.template.count("<strong>") == 3
    assert "background-color:#fdf3c4" in page.template


def test_assets_and_notes(notes):
    page = parse_markdown(notes)
    formulas = [asset for asset in page.assets if asset.kind == "math"]
    images = [asset for asset in page.assets if asset.kind == "image"]
    assert len(formulas) == 3 and not [asset.error for asset in formulas if asset.error]
    assert len(images) == 2 and images[0].zoom == 0.5
    titles = [note.title for note in page.notes]
    assert "Online images are not downloaded" in titles and "Some images were not found" in titles
    assert "image file not found" in page.template and "online image, not downloaded" in page.template


def test_outline_lists_every_heading(notes):
    assert [(h.level, h.text) for h in parse_markdown(notes).headings] == [
        (1, "标题 Title"), (2, "第二级 Second"), (3, "第三级 Third"), (3, "末尾 End"),
    ]


def test_tabs_in_code_become_four_spaces(notes):
    template = parse_markdown(notes).template
    assert "\t" not in template and "    " in template


def test_text_encodings(work):
    for encoding, name in (("utf-8", "a.md"), ("utf-8-sig", "b.md"), ("gb18030", "c.md"), ("utf-16", "d.md")):
        path = work / name
        path.write_text("中文标题", encoding=encoding)
        assert read_text(path) == "中文标题"


def test_markdown_is_detected_by_extension(work):
    empty = work / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert sniff(empty).format == "md"


# -- reopening ----------------------------------------------------------------------------------


def test_reopening_skips_parsing_and_formula_drawing(notes, monkeypatch):
    from james_viewer.core import markdown_engine

    first = parse_markdown(notes)
    monkeypatch.setattr(markdown_engine, "_parse", lambda *a: pytest.fail("the saved result was not used"))
    second = parse_markdown(notes)
    assert second.template == first.template
    assert [h.text for h in second.headings] == [h.text for h in first.headings]
    assert [a.blob for a in second.assets] == [a.blob for a in first.assets]
    assert [n.title for n in second.notes] == [n.title for n in first.notes]


def test_a_changed_file_is_parsed_again(notes):
    parse_markdown(notes)
    notes.write_text(SOURCE + "\n\n## 新增 Added\n", encoding="utf-8")
    assert parse_markdown(notes).headings[-1].text == "新增 Added"


def test_a_changed_image_is_noticed(notes, monkeypatch):
    from james_viewer.core import markdown_engine

    parse_markdown(notes)
    Image.new("RGB", (640, 320), (10, 20, 30)).save(notes.parent / "assets" / "dot.png")
    calls = []
    original = markdown_engine._parse
    monkeypatch.setattr(markdown_engine, "_parse", lambda *a: calls.append(1) or original(*a))
    parse_markdown(notes)
    assert calls, "the saved result should have been ignored"


def test_a_missing_image_that_appears_is_noticed(notes):
    assert "image file not found" in parse_markdown(notes).template
    Image.new("RGB", (40, 40), (10, 20, 30)).save(notes.parent / "assets" / "not-here.png")
    assert "image file not found" not in parse_markdown(notes).template


def test_a_damaged_saved_result_is_ignored(notes):
    from james_viewer.core import paths

    parse_markdown(notes)
    for saved in (paths.cache_dir() / "markdown").glob("*.json"):
        saved.write_text("{ not json", encoding="utf-8")
    assert parse_markdown(notes).headings


# -- very small files ---------------------------------------------------------------------------


def test_very_small_files_all_open(ui, work):
    import pikepdf
    from pptx import Presentation

    tiny_md = work / "tiny.md"
    tiny_md.write_text("x", encoding="utf-8")
    tiny_pdf = work / "tiny.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(10, 10))
    pdf.save(tiny_pdf)
    tiny_deck = work / "tiny.pptx"
    deck = Presentation()
    deck.slides.add_slide(deck.slide_layouts[6])
    deck.save(tiny_deck)
    assert tiny_md.stat().st_size == 1 and tiny_pdf.stat().st_size < 1000
    for path, pages in ((tiny_md, 1), (tiny_pdf, 1), (tiny_deck, 1)):
        view = ui.open(path)
        assert view is not None, ui.told
        assert len(view.document.pages) == pages
    assert not ui.told


# -- opening ------------------------------------------------------------------------------------


def test_opens_and_leaves_the_file_alone(ui, notes):
    before = sha256(notes)
    listing = sorted(p.name for p in notes.parent.rglob("*"))
    view = ui.open(notes)
    reader = _reader(view)
    assert "段落 paragraph 59" in reader.browser.toPlainText()
    assert sha256(notes) == before
    assert sorted(p.name for p in notes.parent.rglob("*")) == listing, "nothing is written beside the file"


def test_formulas_really_draw_ink(ui, notes):
    reader = _reader(ui.open(notes))
    for asset in reader.page.assets:
        if asset.kind != "math":
            continue
        from PySide6.QtCore import QUrl

        image = reader.asset_image(QUrl(f"asset:{asset.key}/{round(asset.width * 4)}/{round((asset.height + asset.pad_top + asset.pad_bottom) * 4)}"))
        inked = sum(1 for y in range(0, image.height(), 2) for x in range(0, image.width(), 2) if image.pixelColor(x, y).alpha() > 128)
        assert inked > 20, f"formula {asset.source!r} drew nothing"


def test_images_load_from_the_assets_folder(ui, notes):
    from PySide6.QtCore import QUrl

    reader = _reader(ui.open(notes))
    image_asset = next(asset for asset in reader.page.assets if asset.kind == "image")
    assert (image_asset.width, image_asset.height) == (400, 200), "natural size, like Typora"
    assert not reader.asset_image(QUrl(f"asset:{image_asset.key}/100/50")).isNull()
    assert f'width="{round(400 * 0.5)}"' in _rendered_img(reader, image_asset.key), "style=zoom:50% is honoured"


def _rendered_img(reader, key: int) -> str:
    import re

    match = re.search(f"\u27e6({key})\u27e7", reader.page.template)
    return reader._expand(match, reader.devicePixelRatioF(), 10_000.0)


# -- zoom ---------------------------------------------------------------------------------------


def test_ctrl_wheel_zooms_in_and_out_and_the_label_follows(ui, notes):
    view = ui.open(notes)
    reader = _reader(view)
    viewport = reader.browser.viewport()
    height = reader.browser.document().size().height()
    _wheel(viewport, 1, Qt.KeyboardModifier.ControlModifier)
    assert abs(reader.zoom - 1.1) < 0.001
    assert view.zoom_label.text() == "110%"
    _settle(reader)
    assert reader.browser.document().size().height() > height * 1.05, "the text really grew"
    _wheel(viewport, -2, Qt.KeyboardModifier.ControlModifier)
    assert abs(reader.zoom - 1 / 1.1) < 0.001
    assert view.zoom_label.text() == "91%"
    _settle(reader)
    assert reader.browser.document().size().height() < height, "the text really shrank"


def test_plain_wheel_scrolls_by_default_and_does_not_zoom(ui, notes):
    reader = _reader(ui.open(notes))
    bar = reader.browser.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    _wheel(reader.browser.viewport(), -3)
    pump(0.05)
    assert reader.zoom == 1.0
    assert bar.value() > 0


def test_wheel_switch_makes_the_plain_wheel_zoom(ui, notes):
    view = ui.open(notes)
    reader = _reader(view)
    bar = reader.browser.verticalScrollBar()
    bar.setValue(0)
    view.wheel_toggle.setChecked(True)
    assert view.wheel_toggle.text() == "Wheel: Zoom"
    _wheel(reader.browser.viewport(), 2)
    assert abs(reader.zoom - 1.21) < 0.001
    _settle(reader)
    view.wheel_toggle.setChecked(False)
    assert view.wheel_toggle.text() == "Wheel: Scroll"
    zoom = reader.zoom
    _wheel(reader.browser.viewport(), -1)
    assert reader.zoom == zoom


def test_wheel_over_the_margin_beside_the_paper_also_works(ui, notes):
    reader = _reader(ui.open(notes))
    _wheel(reader, 1, Qt.KeyboardModifier.ControlModifier, QPoint(2, 100))
    assert abs(reader.zoom - 1.1) < 0.001


def test_touchpad_small_steps_add_up(ui, notes):
    reader = _reader(ui.open(notes))
    for _ in range(4):
        _wheel(reader.browser.viewport(), 0.25, Qt.KeyboardModifier.ControlModifier)
    assert abs(reader.zoom - 1.1) < 0.002


def test_zoom_keeps_the_text_under_the_pointer_in_place(ui, notes):
    reader = _reader(ui.open(notes))
    bar = reader.browser.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    pump(0.05)
    viewport = reader.browser.viewport()
    point = QPoint(viewport.width() // 2, viewport.height() // 3)
    before = reader.browser.cursorForPosition(point).position()
    for notches in (2, -3, 4, -1):
        _wheel(viewport, notches, Qt.KeyboardModifier.ControlModifier, point)
        _settle(reader)
        after = reader.browser.cursorForPosition(point).position()
        assert abs(after - before) < 90, f"reading position jumped from {before} to {after}"


@pytest.mark.parametrize("height_fraction", [0.1, 0.33, 0.5, 0.75, 0.95])
def test_zoom_in_then_back_returns_to_the_same_view(ui, notes, height_fraction):
    """Whatever is under the pointer, including images and blank space, the page must not drift."""
    reader = _reader(ui.open(notes))
    bar = reader.browser.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    pump(0.05)
    viewport = reader.browser.viewport()
    point = QPoint(viewport.width() // 2, int(viewport.height() * height_fraction))
    before = bar.value()
    for notches in (4, -8, 4):
        _wheel(viewport, notches, Qt.KeyboardModifier.ControlModifier, point)
        _settle(reader)
    assert abs(reader.zoom - 1.0) < 0.001
    assert abs(bar.value() - before) <= 3, f"the view drifted from {before} to {bar.value()}"


def test_zoom_with_the_pointer_over_an_image_does_not_jump(ui, work):
    (work / "assets").mkdir()
    Image.new("RGB", (800, 900), (92, 157, 114)).save(work / "assets" / "tall.png")
    path = work / "tall.md"
    text = "\n\n".join(f"段落 {number} words " * 20 for number in range(30))
    path.write_text(f"{text}\n\n![tall](assets/tall.png)\n\n{text}\n", encoding="utf-8")
    reader = _reader(ui.open(path))
    bar = reader.browser.verticalScrollBar()
    bar.setValue(bar.maximum() // 2 - 200)
    pump(0.05)
    viewport = reader.browser.viewport()
    point = QPoint(viewport.width() // 2, viewport.height() // 2)
    before = bar.value()
    for notches in (3, -6, 3):
        _wheel(viewport, notches, Qt.KeyboardModifier.ControlModifier, point)
        _settle(reader)
    assert abs(bar.value() - before) <= 3, f"the view drifted from {before} to {bar.value()}"


def test_zoom_limits_and_reset(ui, notes):
    view = ui.open(notes)
    reader = _reader(view)
    for _ in range(40):
        _wheel(reader.browser.viewport(), 1, Qt.KeyboardModifier.ControlModifier)
    assert reader.zoom == MAX_ZOOM and view.zoom_label.text() == "400%"
    for _ in range(40):
        _wheel(reader.browser.viewport(), -1, Qt.KeyboardModifier.ControlModifier)
    assert reader.zoom == MIN_ZOOM and view.zoom_label.text() == "50%"
    next(s for s in ui.window.findChildren(QShortcut) if s.key() == QKeySequence("Ctrl+0")).activated.emit()
    assert reader.zoom == 1.0


def test_zoom_buttons_and_keyboard(ui, notes):
    view = ui.open(notes)
    reader = _reader(view)
    QTest.mouseClick(view.zoom_in, Qt.MouseButton.LeftButton)
    assert abs(reader.zoom - 1.1) < 0.001
    QTest.mouseClick(view.zoom_out, Qt.MouseButton.LeftButton)
    assert abs(reader.zoom - 1.0) < 0.001
    QTest.mouseClick(view.zoom_in, Qt.MouseButton.LeftButton)
    QTest.mouseClick(view.zoom_label, Qt.MouseButton.LeftButton)
    assert reader.zoom == 1.0
    next(s for s in ui.window.findChildren(QShortcut) if s.key() == QKeySequence("Ctrl+=")).activated.emit()
    assert abs(reader.zoom - 1.1) < 0.001
    next(s for s in ui.window.findChildren(QShortcut) if s.key() == QKeySequence("Ctrl+-")).activated.emit()
    assert abs(reader.zoom - 1.0) < 0.001


def test_images_and_formulas_scale_with_zoom(ui, notes):
    reader = _reader(ui.open(notes))
    image_asset = next(asset for asset in reader.page.assets if asset.kind == "image")
    formula = next(asset for asset in reader.page.assets if asset.kind == "math")

    def width(asset) -> int:
        import re

        tag = reader._expand(re.search(f"\u27e6({asset.key})\u27e7", reader.page.template), 1.0, 100_000.0)
        return int(re.search(r'width="(\d+)"', tag).group(1))

    image_before, formula_before = width(image_asset), width(formula)
    reader.set_zoom(2.0)
    _settle(reader)
    assert width(image_asset) == image_before * 2
    assert abs(width(formula) - formula_before * 2) <= 1


def test_wide_images_never_overflow_the_page(ui, work):
    (work / "assets").mkdir()
    Image.new("RGB", (5000, 500), (92, 157, 114)).save(work / "assets" / "wide.png")
    path = work / "wide.md"
    path.write_text("![wide](assets/wide.png)\n", encoding="utf-8")
    reader = _reader(ui.open(path))
    assert reader.browser.document().size().width() <= reader.browser.viewport().width() + 2
    assert reader.browser.horizontalScrollBar().maximum() == 0


# -- layout -------------------------------------------------------------------------------------


def test_document_layout_shows_and_hides(ui, notes):
    view = ui.open(notes)
    _reader(view)
    assert view.outline.isVisible() and view.outline.items.count() == 4
    ui.window.toggle_layout()
    pump(0.05)
    assert not view.outline.isVisible() and not view.layout_toggle.isChecked()
    ui.window.toggle_layout()
    pump(0.05)
    assert view.outline.isVisible() and view.layout_toggle.isChecked()
    assert any(s.key() == QKeySequence("Ctrl+L") for s in ui.window.findChildren(QShortcut))


def test_clicking_the_outline_jumps_and_the_current_heading_is_marked(ui, notes):
    view = ui.open(notes)
    reader = _reader(view)
    bar = reader.browser.verticalScrollBar()
    assert pump(5, lambda: bool(reader._heading_tops))
    view.outline.chosen.emit(reader.page.headings[-1].anchor)
    pump(0.1)
    assert bar.value() > bar.maximum() * 0.8
    assert view.outline.items.currentRow() == 3


def test_thumbnails_also_show_and_hide_for_pdf(ui, work):
    shutil.copyfile(Path(__file__).parent / "fixtures" / "plain.pdf", work / "plain.pdf")
    view = ui.open(work / "plain.pdf")
    pump(0.1)
    assert view.rail_frame.isVisible()
    ui.window.toggle_layout()
    pump(0.05)
    assert not view.rail_frame.isVisible()
    ui.window.toggle_layout()


# -- full screen --------------------------------------------------------------------------------


def _enter(window):
    window.activateWindow()
    pump(0.05)
    window.present()
    assert pump(5, lambda: window.presenter is not None and window.presenter.isFullScreen())
    pump(0.4)
    return window.presenter


def test_full_screen_shows_only_the_document(ui, notes):
    _reader(ui.open(notes))
    presenter = _enter(ui.window)
    assert isinstance(presenter, MarkdownPresenter)
    assert presenter.geometry() == ui.window.screen().geometry()
    assert presenter.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not presenter.outline.isVisible(), "no layout panel until asked for"
    assert presenter.reader.browser.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor
    QTest.keyClick(presenter.reader.browser, Qt.Key.Key_Escape)
    assert pump(3, lambda: ui.window.presenter is None)
    assert QApplication.overrideCursor() is None


def test_full_screen_zoom_and_layout_then_back(ui, notes):
    view = ui.open(notes)
    reader = _reader(view)
    presenter = _enter(ui.window)
    inner = presenter.reader
    _wheel(inner.browser.viewport(), 3, Qt.KeyboardModifier.ControlModifier)
    assert abs(inner.zoom - 1.331) < 0.002
    _settle(inner)
    QTest.keyClick(inner.browser, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    pump(0.05)
    assert presenter.outline.isVisible()
    QTest.keyClick(inner.browser, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    pump(0.05)
    assert not presenter.outline.isVisible()
    QTest.keyClick(inner.browser, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    assert abs(inner.zoom - 1.4641) < 0.002
    QTest.keyClick(inner.browser, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    assert abs(inner.zoom - 1.331) < 0.002
    _settle(inner)
    QTest.keyClick(inner.browser, Qt.Key.Key_F11)
    assert pump(3, lambda: ui.window.presenter is None)
    pump(0.2)
    assert abs(reader.zoom - 1.331) < 0.002, "the main view keeps the zoom from full screen"
    assert view.zoom_label.text() == "133%"


def test_full_screen_scroll_keys(ui, notes):
    _reader(ui.open(notes))
    presenter = _enter(ui.window)
    browser = presenter.reader.browser
    bar = browser.verticalScrollBar()
    bar.setValue(0)
    QTest.keyClick(browser, Qt.Key.Key_PageDown)
    first = bar.value()
    assert first > 0
    QTest.keyClick(browser, Qt.Key.Key_Down)
    assert bar.value() > first
    QTest.keyClick(browser, Qt.Key.Key_End)
    assert bar.value() == bar.maximum()
    QTest.keyClick(browser, Qt.Key.Key_Home)
    assert bar.value() == 0
    QTest.keyClick(browser, Qt.Key.Key_Escape)
    pump(0.3)


# -- the rest of the app ------------------------------------------------------------------------


def test_arrange_and_merge_decline_markdown_clearly(ui, notes, work):
    _reader(ui.open(notes))
    window = ui.window
    window.set_nav("arrange")
    assert window.nav_mode == "view"
    assert any("Arrange is for PDF and PowerPoint" in message for message in ui.messages)
    window.merge_documents([str(notes), str(notes)])
    assert any("Markdown files cannot be merged" in text for _heading, text in ui.told)
    window.set_nav("split")
    assert not window.split_merge.split_button.isEnabled()
    window.set_nav("view")


def test_save_as_copies_the_file_exactly(ui, notes, work):
    view = ui.open(notes)
    _reader(view)
    out = work / "copy.md"
    ui.window.save_to(view.document, out)
    assert pump(10, lambda: out.exists())
    pump(0.1)
    assert sha256(out) == sha256(notes)


def test_copy_text_from_markdown(ui, notes):
    reader = _reader(ui.open(notes))
    reader.select_all()
    assert reader.has_selection
    ui.window._copy()
    pump(0.1)
    assert "标题 Title" in QApplication.clipboard().text()


def test_protection_page_shows_markdown_notes(ui, notes):
    _reader(ui.open(notes))
    window = ui.window
    window.set_nav("protection")
    pump(0.05)
    assert "Markdown file with 4 headings" in window.protection.subtitle.text()
    window.set_nav("view")


def test_empty_markdown_file_opens(ui, work):
    path = work / "empty.md"
    path.write_text("", encoding="utf-8")
    view = ui.open(path)
    assert view is not None
    pump(0.2)


# -- the user's own notes -----------------------------------------------------------------------


@pytest.mark.skipif(not REAL_FILE.exists(), reason="the real notes are not on this machine")
def test_real_notes_render_completely_and_stay_unchanged(ui):
    before = sha256(REAL_FILE)
    view = ui.open(REAL_FILE)
    reader = _reader(view)
    page = reader.page
    images = [asset for asset in page.assets if asset.kind == "image"]
    formulas = [asset for asset in page.assets if asset.kind == "math"]
    # The owner edits these notes, so check for completeness rather than fixed counts.
    assert len(images) >= 10 and not [asset.source for asset in images if asset.error]
    assert len(formulas) >= 4 and not [asset.source for asset in formulas if asset.error]
    assert len(page.headings) >= 30
    assert "image file not found" not in page.template
    assert not [note for note in page.notes if note.important]
    assert sha256(REAL_FILE) == before


# -- keeping the reader's place ---------------------------------------------------------------------


def _long_notes(work: Path) -> Path:
    out = work / "long.md"
    paragraphs = [f"## 第 {n} 节 Section {n}\n\n" + f"段落 {n} paragraph {n}. " * 40 + "\n" for n in range(120)]
    out.write_text("# Long\n\n" + "\n".join(paragraphs), encoding="utf-8")
    return out


def _top_text(reader: MarkdownReader) -> tuple[int, str]:
    cursor = reader.browser.cursorForPosition(QPoint(reader.browser.viewport().width() // 2, 4))
    return cursor.position(), cursor.block().text()[:24]


def _scroll_to(reader: MarkdownReader, fraction: float) -> None:
    bar = reader.browser.verticalScrollBar()
    bar.setValue(round(bar.maximum() * fraction))
    pump(0.05)


def _assert_same_place(reader: MarkdownReader, before: tuple[int, str]) -> None:
    pump(0.5)
    position, text = _top_text(reader)
    total = reader.browser.document().characterCount()
    assert abs(position - before[0]) < total * 0.01, f"moved from {before} to {(position, text)} of {total}"


def _reader_ready(ui, work) -> tuple:
    view = ui.open(_long_notes(work))
    reader = _reader(view)
    _settle(reader)
    ui.window.set_control_panel(True)
    pump(0.4)
    return view, reader


@pytest.mark.parametrize("fraction", [0.0, 0.2, 0.55, 0.9])
def test_hiding_and_showing_the_layout_keeps_the_place(ui, work, fraction):
    view, reader = _reader_ready(ui, work)
    ui.window.resize(1040, 700)  # narrow enough that the width of the text really changes
    pump(0.4)
    _scroll_to(reader, fraction)
    before = _top_text(reader)
    view.layout_toggle.click()
    _assert_same_place(reader, before)
    assert reader.browser.verticalScrollBar().value() < reader.browser.verticalScrollBar().maximum() or fraction > 0.8
    view.layout_toggle.click()
    _assert_same_place(reader, before)
    ui.window.resize(1400, 880)


@pytest.mark.parametrize("fraction", [0.2, 0.55])
def test_hiding_the_control_panel_keeps_the_place(ui, work, fraction):
    view, reader = _reader_ready(ui, work)
    ui.window.resize(1040, 700)
    pump(0.4)
    _scroll_to(reader, fraction)
    before = _top_text(reader)
    ui.window.set_control_panel(False)
    _assert_same_place(reader, before)
    ui.window.set_control_panel(True)
    _assert_same_place(reader, before)
    ui.window.resize(1400, 880)


def test_resizing_the_window_keeps_the_place(ui, work):
    view, reader = _reader_ready(ui, work)
    ui.window.resize(1400, 880)
    pump(0.4)
    _scroll_to(reader, 0.5)
    before = _top_text(reader)
    for width in (1300, 1200, 1100, 1040):
        ui.window.resize(width, 760)
        pump(0.02)
    _assert_same_place(reader, before)
    ui.window.resize(1400, 880)


@pytest.mark.parametrize("zoomed", [False, True])
def test_leaving_full_screen_stays_where_full_screen_was(ui, work, zoomed):
    view, reader = _reader_ready(ui, work)
    _scroll_to(reader, 0.2)
    _enter(ui.window)
    presenter = ui.window.presenter
    pump(0.4)
    _scroll_to(presenter.reader, 0.7)
    if zoomed:
        presenter.reader.set_zoom(1.3)
        pump(0.3)
    inside = _top_text(presenter.reader)
    QTest.keyClick(presenter.reader.browser, Qt.Key.Key_Escape)
    pump(0.5)
    assert ui.window.presenter is None
    position, _text = _top_text(reader)
    total = reader.browser.document().characterCount()
    assert abs(position - inside[0]) < total * 0.02, f"full screen was at {inside}, the window at {position} of {total}"
    if zoomed:
        reader.set_zoom(1.0)
        pump(0.3)


def test_entering_full_screen_starts_at_the_same_place(ui, work):
    view, reader = _reader_ready(ui, work)
    _scroll_to(reader, 0.6)
    before = _top_text(reader)
    _enter(ui.window)
    presenter = ui.window.presenter
    pump(0.4)
    position, _text = _top_text(presenter.reader)
    total = reader.browser.document().characterCount()
    assert abs(position - before[0]) < total * 0.02, f"window at {before}, full screen at {position} of {total}"
    QTest.keyClick(presenter.reader.browser, Qt.Key.Key_Escape)
    pump(0.3)


def test_the_real_note_keeps_its_place_through_layout_and_full_screen(ui):
    if not REAL_FILE.exists():
        pytest.skip("the real note is not on this PC")
    before_hash = sha256(REAL_FILE)
    view = ui.open(REAL_FILE)
    reader = _reader(view)
    _settle(reader)
    ui.window.resize(1040, 700)
    pump(0.4)
    _scroll_to(reader, 0.5)
    place = _top_text(reader)
    view.layout_toggle.click()
    _assert_same_place(reader, place)
    view.layout_toggle.click()
    _assert_same_place(reader, place)
    ui.window.resize(1400, 880)
    pump(0.4)
    assert sha256(REAL_FILE) == before_hash
