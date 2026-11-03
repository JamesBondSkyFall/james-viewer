# James Viewer: hand-over

Branch `feature/windows-document-viewer`. Nothing is committed yet; wait for the user to ask. The current state and every known limit are in `docs/TEST_REPORT.md`.

## Built and verified

- `dist/James Viewer/James Viewer.exe`
- `dist/James-Viewer-1.0.0-win-x64-portable.zip`
- 155 of 155 automated tests and 29 of 29 real-input checks pass on this build.

## Rerunning the real-input test

It needs an unlocked PC and takes over the keyboard and mouse for about a minute, so tell the user first. Use a folder unzipped from the portable zip:

```bash
.venv\Scripts\python tests\tools\blackbox.py "<unpacked James Viewer folder>" .build\blackbox .build\testdata\new\00-深度学习简介.pptx tests\fixtures\real-chinese.pdf tests\fixtures\plain.pdf "X:\Machine Learning\day05_聚类算法&pytorch框架\02-笔记\01-PyTorch框架\01-PyTorch框架使用.md"
```

## Possible next step

PPS, PPSX and ODP, which the user deferred.

## Hard-won facts

- The office crash came from changing master-page styles right after loading a deck with a chart. The worker waits 0.3 s first, only for decks with embedded objects, and the engine retries up to 3 attempts. Removing that wait brings the crash back.
- `msoffcrypto` must not be used to decrypt `.ppt`: it damages the document stream and skips pictures. `core/security.py` does this itself, and LibreOffice cannot open locked `.ppt` files at all.
- A widget's cursor only applies once the mouse moves, so full screen uses the application-wide override in `CursorAutoHide`.
- `tools/office_removable.json` is the verified trim list. Rebuild the bundle with `tools/build_office.py`, which also creates the finished profile.
- Render-only tweaks live in `_prepare_for_rendering` in `core/pptx_engine.py`, and they never reach saved files. Bump `RENDER_VERSION` in `ui/document.py` when rendering changes, or old cached renders stay in use.
- Clipboard tests and foreground-window tests fail while the PC is locked. That is the lock, not a bug.
- `X:\Study_2026\Codex_Projects\Softs\Slate` runs its own LibreOffice here. Never stop `soffice` by process name.
- The user edits `02-神经网络基础.md` in other apps. Use `01-PyTorch框架使用.md` for testing, read-only.
