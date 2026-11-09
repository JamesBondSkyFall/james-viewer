# James Viewer

A small Windows app, for x64 and ARM64 PCs, for viewing and editing PDF, PowerPoint (PPT, PPTX), Word (DOC, DOCX) and Markdown files, and for organising PDF and PowerPoint pages. It works fully offline, with no account, and needs neither Microsoft Office nor Adobe Acrobat.

## Getting started

1. Unzip the package for your PC anywhere, such as your Documents folder. Nothing is installed.
   - Most PCs: `James-Viewer-1.2.0-win-x64-portable.zip`
   - ARM64 PCs, such as the Surface Laptop 7: `James-Viewer-1.2.0-win-arm64-portable.zip`
2. Run `James Viewer\James Viewer.exe`.
3. Choose files, or drop them anywhere in the window.

Keep the `James Viewer` folder together, because the exe needs the `_internal` and `office` folders beside it.

To check that everything works on a PC, double-click `Self-check.cmd`. It tries each part using its own small test files, then saves a report named `James Viewer self-check.txt` on your Desktop. It opens none of your documents.

## Viewing

- **Show or hide the control panel:** press **Ctrl + B** or click the panel button at the top left. While the panel is hidden, the app's icon stays in the corner and the document gets the extra room. James Viewer remembers your choice.
- **Pages:** use the arrow keys, Page Up and Down, Space, the mouse wheel, or click a thumbnail.
- **Zoom:** use Ctrl + wheel or the minus and plus buttons. For PDF and PowerPoint, click the zoom label for Fit to window, Fit to width or a fixed size.
- **Show or hide the side panel:** press **Ctrl + L** or click the grid button in the toolbar. This shows or hides the thumbnails, or the document layout for Markdown.
- **Tabs:** each document opens in its own tab. Ctrl + Tab switches, and Ctrl + W closes.
- **Copy text:** drag across text on a page, then press Ctrl + C. Ctrl + A selects all text on the page.

The first PowerPoint file you open takes longer, because the office engine has to start. The first slides appear first and the rest fill in behind them. Reopening a file you have opened before is much faster, because the result is saved.

## Markdown

Open any `.md` or `.markdown` file. Images in folders such as `assets` beside the file, formulas, tables, code and Typora's `==highlight==` all display.

- **Zoom:** **Ctrl + mouse wheel**, anywhere from 50% to 400%. The text under your pointer stays in place. Ctrl + 0 returns to 100%.
- **Zoom with the plain wheel:** click **Wheel: Scroll** in the toolbar to switch it to **Wheel: Zoom**. Click again to scroll with the wheel. Ctrl + wheel always zooms.
- **Document layout:** the panel on the left lists the headings. Click one to jump to it. **Ctrl + L** shows or hides the panel.
- **Full screen:** press **F11** or choose **Full Screen**. Only the document shows, and zoom works the same way. Ctrl + L shows the layout panel there too. **Esc** or F11 leaves.

- **Edit on the page:** click **Edit** in the toolbar, then click any paragraph, heading, list, table, code block or formula. That part turns into its Markdown text, right where it was. Change it, then click away or press **Ctrl + Enter** to apply, or press **Esc** to cancel. Ctrl + Z and Ctrl + Y undo and redo.
- **Save:** **Ctrl + S** or **Save** writes your changes back to the file. The first time, James Viewer asks before it replaces the original. The file keeps its own encoding and line endings. **Save As** writes a copy instead.

James Viewer changes a Markdown file only when you save it, and writes nothing else beside it.

## Word

Open any `.doc` or `.docx` file, including password-protected ones. Pages look as they would printed, and zoom, full screen, thumbnails and copying text work as for PDF.

- **Edit text:** click the **T** button. The panel lists each paragraph and table cell on the current page. Change the words and click **Apply**. Each paragraph keeps its formatting, and the pages are laid out again, so text can move onto another page. Undo and redo work.
- **Times New Roman:** tick **Times New Roman for my changes** to set the English letters of the paragraphs you change in Times New Roman. **Times New Roman for all English text** does it for the whole document. Chinese text always keeps its own font.
- **Save As:** DOCX, or PDF of the pages as shown. A `.doc` is converted to DOCX when you first edit it, after asking, and the original `.doc` stays unchanged.
- Paragraphs holding fields, such as page numbers or a table of contents, cannot be edited. Headers and footers are shown but not edited. Documents whose author used Word's Restrict Editing stay read-only. Word pages cannot be rearranged, split or merged.

## Annotating PDFs

In a PDF's toolbar:

- **Highlight** and **Underline:** select text on the page, then click the button.
- **Note:** click the note button, then click on the page and type. Readers see a small note icon and open it to read.
- **Text box:** click the text box button, then click where it should go and type. It shows on the page itself.
- **Draw:** click the pen button, then drag on the page with the mouse or a pen.

Annotations are saved as standard PDF annotations, so Acrobat and web browsers show them, and Acrobat can still edit them. Undo, redo and Save As work as for page changes. A PDF whose author does not allow comments cannot be annotated, and annotations on rotated pages are not supported yet.

## Full screen for PDF and PowerPoint

Press **F11**, double-click a page, or choose **Present**. Only the page is shown, on black, and the cursor hides itself.

- Next: Right, Down, Page Down, Space, Enter, left click or wheel down
- Previous: Left, Up, Page Up, Backspace, right click or wheel up
- First or last: Home or End
- Leave: **Esc** or F11

Slides marked hidden are skipped in full screen.

## Arranging pages and slides

This is for PDF and PowerPoint files. Choose **Arrange** in the sidebar.

- **Reorder:** drag thumbnails. Hold Ctrl or Shift to select several.
- **Copy, Paste, Duplicate, Delete:** use the buttons, or Ctrl + C, Ctrl + V, Ctrl + D and Delete. Copy and paste also works between tabs of the same kind.
- **Add:** a blank PDF page, pages from another PDF, a new slide, or slides from another PowerPoint file.
- **Undo and redo:** Ctrl + Z and Ctrl + Y.

To edit text on a slide, go to **View**, click the **T** button, change the text, and choose **Apply**.

Editing a `.ppt` file first creates a PPTX version, after asking you. Your `.ppt` stays as it was.

## Saving

James Viewer has no plain Save, and **Ctrl + S** opens **Save As**. Your original only changes if you pick its exact name in Save As and then confirm the warning.

## Split and merge

Choose **Split & Merge** in the sidebar.

- **Split:** type ranges such as `1-3, 5, 8-12`, or choose **Use selection**. Pick one new file per range, or one file with all of them.
- **Merge:** add files and drag them into order. PDFs make one PDF. PPT and PPTX files make one PPTX. PDF and PowerPoint files cannot be merged together, and Markdown files cannot be merged.

## Password-protected files

James Viewer asks for the password when you open a protected file and only tries what you type. It never attempts to crack or bypass a password.

To create a copy that opens without a password, open the file, go to **Protection**, and choose **Save Unprotected Copy**.

- A PDF whose author also restricts copying or editing needs the owner password for this. James Viewer respects those restrictions until it is entered.
- Editing a protected file and using Save As keeps its password.

## Compatibility notes

**Protection** also lists anything in the open file that James Viewer cannot reproduce, such as animations, video or substituted fonts.

## Keyboard shortcuts

Press **F1** in the app for the full list.

## Building from source

```bash
python -m venv .venv
.venv\Scripts\pip install PySide6-Essentials pypdfium2 pikepdf msoffcrypto-tool olefile lxml pillow python-pptx pyinstaller pytest markdown-it-py mdit-py-plugins ziamath pygments
```

Download the LibreOffice 26.8.2 x64 MSI from the LibreOffice project and unpack it with `msiexec /a <msi> /qn TARGETDIR=<project>\.build\lo-full`. Then run:

```bash
.venv\Scripts\python tools\build_office.py .build\lo-full .build\office
```

```bash
.venv\Scripts\python tools\build_app.py
```

The results are in `dist\`. See [the test report](docs/TEST_REPORT.md) for what was tested and the known limitations.
