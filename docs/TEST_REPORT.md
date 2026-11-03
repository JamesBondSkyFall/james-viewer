# James Viewer 1.1.1: test report

Tested on Windows 11 x64, 16 GB RAM, 2880×1800 at 200% scaling.

## Status

**All automated tests and real-input checks pass on the x64 package of 1.1.1, it passes its self-check, and all 35 documents in `Learning with notes` open.** The ARM64 package has never been run. The limits that remain are listed under [Not supported or not done](#not-supported-or-not-done).

## Fixed in 1.1.1

- **No PowerPoint file could open on a normal install, in any earlier version.** The default data folder is `AppData\Local\James Viewer`, and an unencoded space in the profile address stopped the office engine from starting. The app showed nothing for minutes. Every earlier test passed because each one set its own data folder without a space. The tests and the real-input test now use a folder with a space and Chinese characters, or the normal default, so this cannot hide again.
- **A failing office engine took minutes to report.** A start that hangs now gives up once, after 45 seconds, and an engine that dies while starting is noticed at once.
- **Closing the window during a render froze the app** until Windows closed it. It now closes in about 0.2 s (5 of 5 in the packaged exe), with no message and nothing left behind.
- **Some decks crashed the office engine at random.** A batch of `course-draft.pptx` with two large pictures crashed 6 of 25 starts, while the deck was closing, after its PDF was saved. The worker now always waits 0.3 s after loading (0 of 25, then 0 of 40), and a crash after saving no longer counts as a failure. Each batch of slides takes 0.3 s longer.

## Real files

Each file was opened in the real window with the normal data folder, and every page was checked. Copies were used, and the originals' hashes are unchanged.

| Files | Result |
|---|---|
| `Learning with notes`: 29 PPTX and 6 Markdown | 35 of 35 open, 1,904 pages, none blank |
| Files I test with: 26 PDF, PPT, PPTX and Markdown | 13 open normally. The other 13 are damaged or locked on purpose: 8 give a clear message and 5 ask for a password |
| The 709-page book | Opens. Pages 2, 5, 706 and 708 are blank in the file itself |

## Fixed in 1.1.0

- **Long PDFs blinked and showed blank pages, using about 1.5 CPU cores non-stop.** The image cache counted a page twice when it was rendered twice, until it believed it was full and kept pushing out what was on screen. Measured on a copy of a 709-page book after paging through it: 787 renders in 4 idle seconds before, 0 after, and the picture no longer changes. Very high zoom could cause the same loop, so a page is now rendered at no more than 36 million pixels.
- **Moving the mouse over blank paper in a PDF raised a hidden error**, and dragging from blank paper could not select text.
- **Markdown lost the reader's place** when hiding the layout (section 66 jumped to section 118, the last), when resizing the window, and when entering or leaving full screen. The place is now held as a character in the document, not as a share of the scroll range.

## Automated tests

185 of 185 pass, driving the real window on the trimmed office bundle, on an unlocked PC.

## ARM64 package

Built on this x64 PC by `tools/build_arm64.py`, because PyInstaller cannot build for another processor. **It has not been run on an ARM64 PC.** Windows here refuses to start it, with error 216, as it should. What has been checked:

- All 275 native files that can be loaded are ARM64, and all their imports resolve to the package or to Windows. The checks only read the files, so they cannot show that the code behaves correctly.
- The same build steps with x64 parts give an exe that runs here and passes 6 of 6 self-checks, including PowerPoint rendering. That also tests cryptography 46.0.3, the version ARM64 has to use.
- The trim list for the office engine was verified on x64 only. LibreOffice's ARM64 build has the same build id, and 56 extra separate libraries were kept rather than removed untested. That is why the package is larger: 528 MB unpacked, 172 MB zip.

`Self-check.cmd` in the package tests each part on the target PC and saves a report on the Desktop.

## Packaged exe with real input

42 of 42 checks pass on the x64 package of 1.1.1, unzipped from the portable zip into a separate folder, using real key presses and real mouse input. The test now finds its data folder the normal way, named `James Viewer`, under a parent folder with a space and Chinese characters. The deck used was a copy of `Convolutional_Neural_Networks_English.pptx`.

Partway through the long-PDF section, another window took the keyboard focus, and the test stopped rather than send keys to it. What took the focus was not identified. That section and the last check were run again on their own, and they passed.

Three checks are new in 1.1.1: PowerPoint renders with the normal data folder, closing during a first render exits at once with no message (0.13 s), and nothing is left behind after that. Ten checks were new in 1.1.0:

- **Keeping your place in Markdown:** hiding and showing the layout, hiding and showing the control panel, and entering and leaving full screen each return to a pixel-identical view (difference 0.0). Scrolling in full screen carries back to the window.
- **Control panel:** the app icon stays at the top left while the panel is hidden.
- **Long PDF, a copy of the 709-page book:** after 280 real page turns, the window stops changing (difference 0.0), uses 0.00 s of CPU over 3 s, and shows a page with content. This shows the app at rest. It does not by itself prove the old loop is gone, because 280 turns may not reach it. The comparison under [Fixed in 1.1.0](#fixed-in-110) does that.

The checks carried over from 1.0.0:

- **F11, PowerPoint:** the window covers the whole 2880×1800 monitor, with no title bar or border. The black bands are pure black, so no taskbar or toolbar shows. The cursor is hidden. Right and Left arrows change slides and return to an identical image. Esc returns to the main window.
- **Real drag and drop** from another window opens the file.
- **Markdown, on `01-PyTorch框架使用.md`:** real Ctrl + wheel zooms in and out, and zooming back returns to a pixel-identical view (difference 0.0). The plain wheel scrolls. F11 covers the monitor with the cursor hidden. Ctrl + wheel works in full screen, and Ctrl + L shows the layout there. Esc leaves. The file's hash is unchanged.
- **Clean-up and isolation:** PDF and Markdown never start the office engine. There are no network connections, no PowerPoint process, no processes left after closing, and temporary files are removed.

Real-input testing found two bugs the other tests could not, both now fixed: the pointer stayed visible when F11 was pressed without moving the mouse, and the page jumped on zoom when the pointer was over an image or blank space.

Moving the mouse in full screen shows the pointer until it has rested for 1.5 seconds.

## Packaged exe, measured from a separate folder

Measured on 1.0.0, except that the real-input run on 1.1.1 confirmed startup at 0.81 s at best. Each batch of slides now takes 0.3 s longer than these first-open times show.

| Measure | Result |
|---|---|
| Startup to window | 0.7–0.85 s |
| PDF or Markdown open, including app start | 1.0–1.25 s |
| First slides of a PowerPoint file, first open | 2.3–2.7 s |
| Whole deck, first open | 3.7 s (11 slides), 10.6 s (98), 12.5 s (125) |
| Any file reopened, including app start | About 1 s, office engine not started |
| Markdown parsing on reopen (from source, 111-formula file) | 0.715 s down to 0.01 s |
| Markdown zoom step, 54,000-character file (from source) | About 125 ms |
| Memory | 177 MB idle, 200–225 MB with a document, plus up to about 330 MB for the office engine while in use |
| Size | 502 MB unpacked, 184 MB zip |
| Processes left after closing | None |

The office engine stops after 5 idle minutes. "Fast startup" is met. "Lightweight" is only partly met: PowerPoint rendering needs LibreOffice, which accounts for 397 MB of the 502 MB, trimmed from about 1,150 MB.

## Rendering compared with real PowerPoint

Each slide is compared with PowerPoint's own image, using a blurred comparison where 100 means identical. A one-pixel text shift costs little, and a missing picture costs a lot. PowerPoint was used only for testing.

| Deck | Slides | Average | Below 97 |
|---|---:|---:|---:|
| MySQL函数 | 105 | 99.27 | 0 |
| 01_MySQL拓展 | 48 | 99.04 | 0 |
| 02_FineBI | 61 | 99.36 | 0 |
| 00-深度学习简介 | 11 | 99.01 | 0 |
| 01-PyTorch基本使用 | 98 | 98.77 | 1 |
| 02-神经网络基础 | 125 | 98.70 | 4 |

I looked at the lowest-scoring slides. The differences are line spacing and spacing around Chinese punctuation, which can move a line break. Scores were measured before the list-number fix below, which only changes affected slides.

Three problems found by comparing and fixed: false line breaks in no-wrap text, extra gaps between Chinese and Latin text, and repeated list numbers (②②② instead of ②③④).

## Tested and working

**Files:** content-based detection of PDF, PPT, PPTX and Markdown. Damaged, empty, truncated and misnamed files give a clear message. Very small files open.

**Viewing:** tabs, keyboard, wheel and thumbnail navigation, zoom, fit to window, show or hide thumbnails, text selection and copy, English and Simplified Chinese.

**Full screen:** verified with real input as above. Automated tests also cover aspect ratio, and hidden slides being skipped.

**Markdown, on your files, read-only:** all 13 images in `01-PyTorch框架使用.md` and all 63 in `02-神经网络基础.md` load from `assets`. All formulas draw (4 and 111). Also tables, including inside lists and quotes, code colouring, `==highlight==`, `style="zoom:40%"`, and Chinese bold that strict parsers leave as literal asterisks. File hashes were unchanged across each run. `02-神经网络基础.md` changed once, at a time Typora and Obsidian both had it open. James Viewer has no code path that writes to an opened Markdown file.

**Markdown zoom:** Ctrl + wheel, plain wheel through the toolbar switch, touchpad steps, buttons, keys, 50–400% limits, images and formulas scale, zoom carries in and out of full screen, no drift when zooming back, whatever is under the pointer.

**Editing:** add, delete, duplicate, reorder, text edit keeping formatting, undo and redo, copy and paste between tabs, Save As.

**Split and merge:** both families, never overwriting. PDF outlines are kept. Decks of different sizes scale without distortion.

**Passwords:** PDF, PPTX and PPT, with right and wrong passwords and cancelling. PDF owner restrictions are respected. Save Unprotected Copy works. Decrypted pictures from a real locked `.ppt` matched the unlocked copy byte for byte across 9.5 MB.

**Opened by real PowerPoint without error:** reversed, duplicated, split, text-edited, new-slide, a 234-slide merge of your three decks, a merge of different slide sizes, a re-protected PPTX, and unprotected PPTX and PPT copies.

**Offline:** no network connections with all processes running. No PowerPoint process started.

**Originals:** hashes unchanged in every test that opens them. Replacing an original needs an explicit confirmation.

**Stability:** with the wait applied to chart decks, 120 renders with fresh engine starts had 0 failures. The wider 1.1.1 wait gave 0 failures in 40 more on a deck with large pictures. A crash would restart the engine and retry, up to 3 attempts.

## Not supported or not done

- **The ARM64 package has not been run.** See [ARM64 package](#arm64-package).
- **PPS, PPSX and ODP are not supported yet**, deferred as you asked.
- At very high zoom, PDF and slide pages can look slightly soft, from about 430% on a Letter page at 200% display scaling.
- **Not tested on a PC without Microsoft Office** or without the Visual C++ runtime. The app does not use Office, and it bundles the runtime, but this PC has Office installed, so neither is proven.
- **No installer.** The portable zip is the delivery.
- **Multiple monitors not tested.** Only one is attached.
- Animations, transitions, audio and video do not play. Slides show their final state.
- Charts are redrawn by LibreOffice, so label positions can differ slightly. Newer chart types such as waterfall may be simplified.
- `.ppt` files are edited through a PPTX conversion, after asking, and some formatting may shift. Only standard PowerPoint encryption (RC4 CryptoAPI) is tested.
- Slide text inside charts and SmartArt cannot be edited.
- Copy and paste of slides works only within James Viewer, not to or from PowerPoint.
- Duplicated or merged slides do not keep comments. Merged decks do not keep embedded fonts.
- Deleting PDF pages removes the file's accessibility tags, and links to deleted pages. Merged PDF form fields were not tested.
- Markdown is read-only. Online images are not downloaded. Formulas use a LaTeX subset, and any unsupported one shows as source text with a note. Advanced HTML and CSS look simpler than in a browser.
- Plain wheel scrolls by default so long files stay readable. Plain-wheel zoom is the toolbar switch, and it is not remembered between runs.
