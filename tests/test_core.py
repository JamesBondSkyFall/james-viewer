"""File-level behaviour: detection, ranges, PDF operations, decks, and passwords."""

import shutil
from pathlib import Path

import pikepdf
import pytest
from pptx import Presentation

from conftest import FIXTURES, OWNER_PASSWORD, PASSWORD
from james_viewer.core.errors import CorruptFile, ViewerError, PasswordRequired, UnsupportedFile, WrongPassword
from james_viewer.core.pdf_engine import PdfPageSpec, merge_pdfs, read_access, remove_protection, write_pdf
from james_viewer.core.pptx_engine import SlideSpec, build_deck, list_text_blocks, read_deck_info
from james_viewer.core.ranges import format_ranges, parse_ranges
from james_viewer.core.security import decrypt_office, decrypt_ppt, encrypt_pptx
from james_viewer.core.sniff import sniff


@pytest.mark.parametrize(
    "name, fmt, encrypted",
    [
        ("plain.pdf", "pdf", False),
        ("locked-aes256.pdf", "pdf", True),
        ("rich.pptx", "pptx", False),
        ("rich-locked.pptx", "pptx", True),
        ("rich.ppt", "ppt", False),
        ("rich-locked.ppt", "ppt", True),
    ],
)
def test_detects_format_and_protection(name, fmt, encrypted):
    kind = sniff(FIXTURES / name)
    assert (kind.format, kind.encrypted) == (fmt, encrypted)


def test_detects_content_not_extension(work):
    renamed = work / "really-a-pptx.ppt"
    shutil.copyfile(FIXTURES / "rich.pptx", renamed)
    assert sniff(renamed).format == "pptx"


@pytest.mark.parametrize(
    "name, error",
    [
        ("corrupt-garbage.pdf", CorruptFile),
        ("empty.pdf", CorruptFile),
        ("text-named.pdf", CorruptFile),
        ("corrupt-truncated.pptx", CorruptFile),
        ("corrupt-garbage.pptx", CorruptFile),
        ("corrupt-garbage.ppt", CorruptFile),
    ],
)
def test_damaged_files_give_a_clear_error(name, error):
    with pytest.raises(error) as caught:
        sniff(FIXTURES / name)
    assert name in str(caught.value)


def test_truncated_ppt_is_reported_or_opens_as_ppt():
    try:
        assert sniff(FIXTURES / "corrupt-truncated.ppt").format == "ppt"
    except CorruptFile:
        pass


def test_other_files_are_rejected(work):
    text = work / "notes.txt"
    text.write_text("hello", encoding="utf-8")
    with pytest.raises(UnsupportedFile):
        sniff(text)


def test_range_parsing():
    assert parse_ranges("1-3, 5", 9) == [[0, 1, 2], [4]]
    assert parse_ranges("8-", 9) == [[7, 8]]
    assert parse_ranges("-2", 9) == [[0, 1]]
    assert format_ranges([0, 1, 2, 4, 7, 8]) == "1-3, 5, 8-9"
    for bad in ("", "abc", "0", "10", "3-1"):
        with pytest.raises(ViewerError):
            parse_ranges(bad, 9)


# -- PDF ------------------------------------------------------------------------------------------


def _page_labels(path, password=""):
    """Each page's visible text, read back through PDFium."""
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path), password=password or None)
    labels = []
    for index in range(len(document)):
        page = document[index]
        text = page.get_textpage()
        labels.append(text.get_text_range().strip())
        text.close()
        page.close()
    document.close()
    return labels


def test_pdf_reorder_delete_duplicate(work):
    source = str(FIXTURES / "plain.pdf")
    specs = [PdfPageSpec(source, None, i) for i in (2, 0, 0, 5)]
    out = work / "out.pdf"
    write_pdf(PdfPageSpec(source, None, 0), specs, out)
    assert _page_labels(out) == ["Page 3 of P", "Page 1 of P", "Page 1 of P", "Page 6 of P"]


def test_pdf_deleted_pages_do_not_stay_in_the_file(work):
    source = str(FIXTURES / "plain.pdf")
    out = work / "out.pdf"
    write_pdf(PdfPageSpec(source, None, 0), [PdfPageSpec(source, None, 1)], out)
    with pikepdf.open(out) as pdf:
        stored = [obj for obj in pdf.objects if isinstance(obj, pikepdf.Dictionary) and obj.get("/Type") == pikepdf.Name.Page]
        assert len(pdf.pages) == 1 and len(stored) == 1
        with pdf.open_outline() as outline:
            assert [item.title for item in outline.root] == ["P page 2"]


def test_pdf_outline_survives_reorder(work):
    source = str(FIXTURES / "plain.pdf")
    out = work / "out.pdf"
    write_pdf(PdfPageSpec(source, None, 0), [PdfPageSpec(source, None, i) for i in (5, 4, 3, 2, 1, 0)], out)
    assert _page_labels(out)[0] == "Page 6 of P"
    with pikepdf.open(out) as pdf:
        keys = {tuple(page.obj.objgen): number for number, page in enumerate(pdf.pages)}
        with pdf.open_outline() as outline:
            first = outline.root[0]
            assert first.title == "P page 1"
            assert keys[tuple(first.destination[0].objgen)] == 5


def test_pdf_insert_from_another_file(work):
    source, other = str(FIXTURES / "plain.pdf"), str(FIXTURES / "second.pdf")
    out = work / "out.pdf"
    specs = [PdfPageSpec(source, None, 0), PdfPageSpec(other, None, 1), PdfPageSpec(source, None, 1)]
    write_pdf(PdfPageSpec(source, None, 0), specs, out)
    assert _page_labels(out) == ["Page 1 of P", "Page 2 of Q", "Page 2 of P"]


def test_pdf_merge_keeps_every_page_and_outlines(work):
    out = work / "merged.pdf"
    inputs = [(str(FIXTURES / "plain.pdf"), None), (str(FIXTURES / "second.pdf"), None)]
    merge_pdfs(inputs, ["plain", "second"], out)
    labels = _page_labels(out)
    assert len(labels) == 9 and labels[0] == "Page 1 of P" and labels[6] == "Page 1 of Q"
    with pikepdf.open(out) as pdf:
        with pdf.open_outline() as outline:
            assert [item.title for item in outline.root] == ["plain", "second"]
            assert [child.title for child in outline.root[1].children][:1] == ["Q page 1"]


def test_pdf_merge_with_chinese_paths_and_password(work):
    chinese = work / "线性回归 合并.pdf"
    locked = work / "受保护.pdf"
    shutil.copyfile(FIXTURES / "plain.pdf", chinese)
    shutil.copyfile(FIXTURES / "locked-aes256.pdf", locked)
    out = work / "结果.pdf"
    merge_pdfs([(str(chinese), None), (str(locked), PASSWORD)], ["a", "b"], out)
    assert len(_page_labels(out)) == 12


def test_real_pdf_chinese_text_extracts():
    assert "线性" in _page_labels(FIXTURES / "real-chinese.pdf")[0]


def test_pdf_password_rules():
    with pytest.raises(PasswordRequired):
        read_access(FIXTURES / "locked-aes256.pdf", None)
    with pytest.raises(WrongPassword):
        read_access(FIXTURES / "locked-aes256.pdf", "wrong")
    assert read_access(FIXTURES / "locked-aes256.pdf", PASSWORD).encrypted


def test_owner_restrictions_are_respected(work):
    access = read_access(FIXTURES / "restricted-owner-only.pdf", None)
    assert access.encrypted and not access.can_assemble and not access.can_extract
    assert not access.may_remove_protection
    with pytest.raises(ViewerError):
        remove_protection(str(FIXTURES / "restricted-owner-only.pdf"), None, access, work / "x.pdf")
    assert not (work / "x.pdf").exists()

    user_only = read_access(FIXTURES / "restricted-both.pdf", PASSWORD)
    assert not user_only.may_remove_protection
    owner = read_access(FIXTURES / "restricted-both.pdf", OWNER_PASSWORD)
    assert owner.owner_verified and owner.may_remove_protection


def test_unprotected_pdf_copy_opens_without_password(work):
    access = read_access(FIXTURES / "locked-aes256.pdf", OWNER_PASSWORD)
    out = work / "open.pdf"
    remove_protection(str(FIXTURES / "locked-aes256.pdf"), OWNER_PASSWORD, access, out)
    assert sniff(out).encrypted is False
    assert _page_labels(out)[0] == "Page 1 of P"


def test_edited_protected_pdf_keeps_its_password(work):
    source = str(FIXTURES / "locked-aes256.pdf")
    out = work / "still-locked.pdf"
    write_pdf(PdfPageSpec(source, PASSWORD, 0), [PdfPageSpec(source, PASSWORD, 1)], out, encryption=True)
    assert sniff(out).encrypted is True
    with pytest.raises(PasswordRequired):
        read_access(out, None)
    assert _page_labels(out, PASSWORD) == ["Page 2 of P"]


# -- PPTX -----------------------------------------------------------------------------------------


def _titles(path):
    deck = Presentation(path)
    return [slide.shapes.title.text if slide.shapes.title is not None else "" for slide in deck.slides]


def test_deck_info():
    info = read_deck_info(FIXTURES / "rich.pptx")
    assert len(info.slides) == 7
    assert [s.hidden for s in info.slides].count(True) == 1 and info.slides[5].hidden
    assert not info.modify_protected


def test_deck_reorder_delete_duplicate(work):
    source = str(FIXTURES / "rich.pptx")
    out = work / "out.pptx"
    build_deck(source, [SlideSpec(source, i) for i in (1, 0, 1)], out)
    titles = _titles(out)
    assert len(titles) == 3 and titles[0] == titles[2] == "Bullets A" and titles[1].startswith("Deck A")


def test_duplicate_owns_its_chart_and_notes(work):
    source = str(FIXTURES / "rich.pptx")
    out = work / "out.pptx"
    build_deck(source, [SlideSpec(source, 3), SlideSpec(source, 3), SlideSpec(source, 6), SlideSpec(source, 6)], out)
    deck = Presentation(out)
    charts = [shape.chart.part.partname for slide in list(deck.slides)[:2] for shape in slide.shapes if shape.has_chart]
    assert len(charts) == 2 and charts[0] != charts[1]
    notes = [slide.notes_slide.notes_text_frame.text for slide in list(deck.slides)[2:]]
    assert notes == ["Speaker notes 演讲者备注"] * 2


def test_text_edit_keeps_run_formatting(work):
    source = str(FIXTURES / "rich.pptx")
    spec = SlideSpec(source, 1)
    blocks = list_text_blocks(source, spec)
    body = next(block for block in blocks if "First bullet" in block.text)
    edited = body.text.replace("First bullet 第一点", "First point 第一点 changed")
    out = work / "out.pptx"
    build_deck(source, [SlideSpec(source, 1, edits=((body.index, edited),))], out)
    paragraph = Presentation(out).slides[0].placeholders[1].text_frame.paragraphs[0]
    assert paragraph.text == "First point 第一点 changed"
    run = paragraph.runs[0]
    assert run.font.bold is True and run.font.name == "Microsoft YaHei"
    assert str(run.font.color.rgb) == "2E7D4F"


def test_text_edit_adds_and_removes_paragraphs(work):
    source = str(FIXTURES / "rich.pptx")
    blocks = list_text_blocks(source, SlideSpec(source, 1))
    body = next(block for block in blocks if "First bullet" in block.text)
    lines = body.text.split("\n")
    changed = "\n".join([lines[0], "Inserted 插入", *lines[2:]])
    out = work / "out.pptx"
    build_deck(source, [SlideSpec(source, 1, edits=((body.index, changed),))], out)
    assert [p.text for p in Presentation(out).slides[0].placeholders[1].text_frame.paragraphs] == changed.split("\n")


def test_table_cells_are_editable(work):
    source = str(FIXTURES / "rich.pptx")
    blocks = list_text_blocks(source, SlideSpec(source, 2))
    assert blocks[0].name == "Table cell 1.1"
    out = work / "out.pptx"
    build_deck(source, [SlideSpec(source, 2, edits=((blocks[0].index, "已修改"),))], out)
    table = next(shape for shape in Presentation(out).slides[0].shapes if shape.has_table).table
    assert table.cell(0, 0).text == "已修改"


def test_new_slide_from_layout(work):
    source = str(FIXTURES / "rich.pptx")
    info = read_deck_info(source)
    layout = info.slides[1].layout_part
    new = SlideSpec(source, -1, layout=layout)
    blocks = list_text_blocks(source, new)
    out = work / "out.pptx"
    build_deck(source, [SlideSpec(source, 0), SlideSpec(source, -1, layout=layout, edits=((blocks[0].index, "新标题"),))], out)
    assert _titles(out)[1] == "新标题"


def test_merge_decks(work):
    first, second = str(FIXTURES / "rich.pptx"), str(FIXTURES / "second.pptx")
    specs = [SlideSpec(first, i) for i in range(7)] + [SlideSpec(second, i) for i in range(7)]
    out = work / "merged.pptx"
    build_deck(first, specs, out)
    titles = _titles(out)
    assert len(titles) == 14 and titles[7].startswith("Deck B") and titles[10] == "Chart B"


def test_merge_different_slide_size_scales_without_distortion(work):
    wide, standard = str(FIXTURES / "rich.pptx"), str(FIXTURES / "standard43.pptx")
    out = work / "merged.pptx"
    report = build_deck(wide, [SlideSpec(wide, 4), SlideSpec(standard, 4)], out)
    assert report.warnings and "scaled" in report.warnings[0]
    deck = Presentation(out)
    assert deck.slide_width == Presentation(wide).slide_width
    assert len(deck.slide_masters) == 2
    picture = next(shape for shape in deck.slides[1].shapes if shape.shape_type == 13)
    source_picture = next(shape for shape in Presentation(standard).slides[4].shapes if shape.shape_type == 13)
    assert abs(picture.width / picture.height - source_picture.width / source_picture.height) < 0.01


def test_repeated_start_at_counts_up_like_powerpoint():
    from lxml import etree

    from james_viewer.core.pptx_engine import _spell_out_list_numbers

    numbered = '<a:pPr><a:buAutoNum type="circleNumDbPlain" startAt="2"/></a:pPr><a:r><a:t>item</a:t></a:r>'
    blank = '<a:pPr><a:buAutoNum type="circleNumDbPlain" startAt="2"/></a:pPr>'
    plain = '<a:pPr><a:buAutoNum type="circleNumDbPlain"/></a:pPr><a:r><a:t>first</a:t></a:r>'
    paragraphs = [plain, "", numbered, blank, numbered, blank, numbered]
    xml = (
        '<p:txBody xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:bodyPr/>'
        + "".join(f"<a:p>{item}</a:p>" for item in paragraphs)
        + "</p:txBody>"
    )
    root = etree.fromstring(f'<root xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">{xml}</root>')
    assert _spell_out_list_numbers(root) is True
    starts = [node.get("startAt") for node in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}buAutoNum")]
    assert starts == [None, "2", "2", "3", "2", "4"]  # blank items keep theirs, and they show no number


def test_rendering_tweaks_never_reach_a_saved_file(work):
    from james_viewer.core.opc import Package

    source = str(FIXTURES / "rich.pptx")
    out = work / "saved.pptx"
    build_deck(source, [SlideSpec(source, 0), SlideSpec(source, 1)], out)
    with Package(source) as original, Package(out) as saved:
        for name in ("/ppt/slideMasters/slideMaster1.xml", "/ppt/slideLayouts/slideLayout1.xml"):
            assert original.parts[name].blob == saved.parts[name].blob, "untouched parts stay byte-identical"


def test_build_refuses_empty_deck(work):
    with pytest.raises(ViewerError):
        build_deck(str(FIXTURES / "rich.pptx"), [], work / "x.pptx")


# -- Office passwords -----------------------------------------------------------------------------


def test_pptx_decrypt_with_right_password(work):
    out = work / "open.pptx"
    decrypt_office(FIXTURES / "rich-locked.pptx", PASSWORD, out)
    kind = sniff(out)
    assert kind.encrypted is False and kind.format == "pptx"


def test_pptx_decrypt_rejects_wrong_password(work):
    out = work / "x.pptx"
    with pytest.raises(WrongPassword):
        decrypt_office(FIXTURES / "rich-locked.pptx", "not-the-password", out)
    assert not out.exists()


def test_ppt_decrypt_recovers_pictures_exactly(work):
    import olefile

    out = work / "open.ppt"
    decrypt_ppt(FIXTURES / "rich-locked.ppt", PASSWORD, out)
    assert sniff(out).encrypted is False
    with olefile.OleFileIO(str(out)) as opened, olefile.OleFileIO(str(FIXTURES / "rich.ppt")) as plain:
        assert opened.openstream("Pictures").read() == plain.openstream("Pictures").read()


def test_ppt_decrypt_rejects_wrong_password(work):
    out = work / "x.ppt"
    with pytest.raises(WrongPassword):
        decrypt_ppt(FIXTURES / "rich-locked.ppt", "not-the-password", out)
    assert not out.exists()


def test_pptx_can_be_re_encrypted(work):
    out = work / "locked.pptx"
    encrypt_pptx(FIXTURES / "rich.pptx", PASSWORD, out)
    assert sniff(out).encrypted is True
    back = work / "back.pptx"
    decrypt_office(out, PASSWORD, back)
    assert len(Presentation(back).slides) == 7
    assert Path(back).exists()
