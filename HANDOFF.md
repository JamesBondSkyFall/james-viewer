# James Viewer: hand-over

Branch `feature/windows-document-viewer`. Version 1.0.0 is commit `7054319`. Version 1.1.0 is not committed yet; wait for the user to ask. The current state and every known limit are in `docs/TEST_REPORT.md`.

## Built

- `dist/1.1.0/x64/James Viewer/James Viewer.exe` and `dist/1.1.0/James-Viewer-1.1.0-win-x64-portable.zip`
- `dist/1.1.0/arm64/James Viewer/James Viewer.exe` and `dist/1.1.0/James-Viewer-1.1.0-win-arm64-portable.zip`, which have never been run
- `dist/James Viewer` is the old 1.0.0. Leave it alone while the user may still have it open.

## Waiting on the user

- The result of `Self-check.cmd` on the Surface Laptop 7.

## Rebuilding

`tools/build_app.py` builds x64. `tools/build_arm64.py` builds ARM64 on this x64 PC, and `--control` runs the same steps with x64 parts so the result can be run here. Its parts folders, `.build/arm64` and `.build/arm64-control`, are listed at the top of the script. They are not in git.

## Rerunning the real-input test

It needs an unlocked PC and takes over the keyboard and mouse for about a minute, so tell the user first. Use a folder unzipped from the portable zip:

```bash
.venv\Scripts\python tests\tools\blackbox.py "<unpacked James Viewer folder>" .build\blackbox .build\testdata\new\00-深度学习简介.pptx tests\fixtures\real-chinese.pdf tests\fixtures\plain.pdf "X:\Machine Learning\day05_聚类算法&pytorch框架\02-笔记\01-PyTorch框架\01-PyTorch框架使用.md" .build\testdata\bug\ember.pdf
```

`ember.pdf` is a copy of the user's 709-page `H:\Others\战争的余烬.pdf`. 39 of 39 checks passed on 1.1.0.

## Possible next step

PPS, PPSX and ODP, which the user deferred.

## Hard-won facts

- The office crash came from changing master-page styles right after loading a deck with a chart. The worker waits 0.3 s first, only for decks with embedded objects, and the engine retries up to 3 attempts. Removing that wait brings the crash back.
- `msoffcrypto` must not be used to decrypt `.ppt`: it damages the document stream and skips pictures. `core/security.py` does this itself, and LibreOffice cannot open locked `.ppt` files at all.
- A widget's cursor only applies once the mouse moves, so full screen uses the application-wide override in `CursorAutoHide`.
- `tools/office_removable.json` is the verified trim list. Rebuild the bundle with `tools/build_office.py`, which also creates the finished profile.
- Render-only tweaks live in `_prepare_for_rendering` in `core/pptx_engine.py`, and they never reach saved files. Bump `RENDER_VERSION` in `ui/document.py` when rendering changes, or old cached renders stay in use.
- After a width change, Qt lays out a long document only down to the view, so the scroll range is wrong until the layout finishes. Never save a place as a share of that range. `MarkdownReader.place()` holds a character instead, taken before the browser is resized.
- Windows ARM64 facts: cryptography stopped publishing ARM64 wheels after 46.0.3. Python's ARM64 embeddable zip names `libcrypto-3-arm64.dll` differently, and its `vcruntime140_1.dll` is x64. The `vccorlib140.dll` in the Qt ARM64 wheel is 32-bit ARM. The LibreOffice ARM64 MSI carries only the x86 and x64 C++ runtime. `verify()` in `tools/build_arm64.py` catches all of these.
- Clipboard tests and foreground-window tests fail while the PC is locked. That is the lock, not a bug.
- `X:\Study_2026\Codex_Projects\Softs\Slate` runs its own LibreOffice here. Never stop `soffice` by process name.
- The user edits `02-神经网络基础.md` in other apps. Use `01-PyTorch框架使用.md` for testing, read-only.
