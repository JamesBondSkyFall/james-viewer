# James Viewer: hand-over

Branch `feature/windows-document-viewer`. Version 1.0.0 is commit `7054319`, 1.1.0 is `eb8d441`, and 1.1.1 is `1661407`. 1.2.0 (Word, PDF annotations, Markdown editing) is not committed yet. Commit only when the user asks. The current state and every known limit are in `docs/TEST_REPORT.md`.

## Built

- `dist/1.2.0/x64/James Viewer/James Viewer.exe` and `dist/1.2.0/James-Viewer-1.2.0-win-x64-portable.zip`. No 1.2.0 ARM64 build: the user said to leave ARM64 until they ask.
- `dist/1.1.1/x64/James Viewer/James Viewer.exe` and `dist/1.1.1/James-Viewer-1.1.1-win-x64-portable.zip`
- `dist/1.1.1/arm64/James Viewer/James Viewer.exe` and `dist/1.1.1/James-Viewer-1.1.1-win-arm64-portable.zip`, which have never been run
- `dist/James Viewer` (1.0.0) and `dist/1.1.0` are older, and neither can open PowerPoint files on a normal install. Leave them alone while the user may still have one open.

## Waiting on the user

- The result of `Self-check.cmd` on the Surface Laptop 7.

## Rebuilding

`tools/build_app.py` builds x64. `tools/build_arm64.py` builds ARM64 on this x64 PC, and `--control` runs the same steps with x64 parts so the result can be run here. Its parts folders, `.build/arm64` and `.build/arm64-control`, are listed at the top of the script. They are not in git.

## Rerunning the real-input test

It needs an unlocked PC and takes over the keyboard and mouse for about a minute, so tell the user first. Use a folder unzipped from the portable zip:

```bash
.venv\Scripts\python tests\tools\blackbox.py "<unpacked James Viewer folder>" .build\blackbox .build\testdata\new\00-深度学习简介.pptx tests\fixtures\real-chinese.pdf tests\fixtures\plain.pdf "X:\Machine Learning\day05_聚类算法&pytorch框架\02-笔记\01-PyTorch框架\01-PyTorch框架使用.md" .build\testdata\bug\ember.pdf
```

`ember.pdf` is a copy of the user's 709-page `H:\Others\战争的余烬.pdf`. 42 of 42 checks passed on 1.1.1, using a copy of the user's CNN deck in place of `00-深度学习简介.pptx`. The test refuses to send keys if another window takes the focus, so it can stop partway while the PC is in use.

## Possible next step

PPS, PPSX and ODP, which the user deferred.

## Hard-won facts

- Test with the real data folder. `AppData\Local\James Viewer` has a space, and an unencoded space in the profile address stopped the engine from starting, so no PowerPoint file could open on a normal install. Tests that set their own folder without a space hid this until 1.1.1. Pass `Path.as_uri()` to LibreOffice, and set `LOCALAPPDATA` in tests to use the normal path.
- The office keeps working after a load reports it has finished. Going straight on crashes it on some decks, while changing master-page styles (a chart) or while closing (large pictures). The worker always waits 0.3 s, and a crash after saving does not count as a failure. Removing that wait brings the crash back. These crashes depend on timing, so measure over many starts with `tests/tools/office_repeat.py`.
- `msoffcrypto` must not be used to decrypt `.ppt`: it damages the document stream and skips pictures. `core/security.py` does this itself, and LibreOffice cannot open locked `.ppt` files at all.
- A widget's cursor only applies once the mouse moves, so full screen uses the application-wide override in `CursorAutoHide`.
- `tools/office_removable.json` is the verified trim list. Rebuild the bundle with `tools/build_office.py`, which also creates the finished profile.
- Render-only tweaks live in `_prepare_for_rendering` in `core/pptx_engine.py`, and they never reach saved files. Bump `RENDER_VERSION` in `ui/document.py` when rendering changes, or old cached renders stay in use.
- After a width change, Qt lays out a long document only down to the view, so the scroll range is wrong until the layout finishes. Never save a place as a share of that range. `MarkdownReader.place()` holds a character instead, taken before the browser is resized.
- Windows ARM64 facts: cryptography stopped publishing ARM64 wheels after 46.0.3. Python's ARM64 embeddable zip names `libcrypto-3-arm64.dll` differently, and its `vcruntime140_1.dll` is x64. The `vccorlib140.dll` in the Qt ARM64 wheel is 32-bit ARM. The LibreOffice ARM64 MSI carries only the x86 and x64 C++ runtime. `verify()` in `tools/build_arm64.py` catches all of these.
- Clipboard tests and foreground-window tests fail while the PC is locked. That is the lock, not a bug.
- `X:\Study_2026\Codex_Projects\Softs\Slate` runs its own LibreOffice here. Never stop `soffice` by process name.
- Word: `core/docx_engine.py` edits the XML and LibreOffice only lays it out (`wordpdf`, `docx` worker ops). The office bundle keeps the Writer parts in `NEEDED_FOR_WORD` in `tools/build_office.py`; `.build/office-slides-only` is the old smaller bundle. Times New Roman goes in `ascii` and `hAnsi` only, never `eastAsia`, so Chinese keeps its font.
- The test harness must answer yes when closing a dirty tab. A Markdown document tracks its own text, so marking pages saved does not make it clean, and a real "Quit without saving?" dialog then waits forever at the end of the run.
- The user edits `02-神经网络基础.md` in other apps. Use `01-PyTorch框架使用.md` for testing, read-only.
