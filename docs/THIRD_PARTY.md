# Third-party components

James Viewer bundles these open-source components. All run locally, and none of them is modified.

| Component | Purpose | Licence |
|---|---|---|
| LibreOffice 26.8.2 (trimmed copy, in `office/`) | Renders and converts PowerPoint files | MPL-2.0, plus the licences in `office/LICENSE.html` |
| Qt 6 through PySide6-Essentials | User interface | LGPL-3.0 |
| PDFium through pypdfium2 | Renders PDF pages and reads their text | Apache-2.0 and BSD-3-Clause |
| qpdf through pikepdf | PDF page operations, merging and protection | Apache-2.0 (qpdf), MPL-2.0 (pikepdf) |
| msoffcrypto-tool | Checks PowerPoint passwords, decrypts and encrypts PPTX | MIT |
| cryptography | Cipher routines used by msoffcrypto-tool | Apache-2.0 or BSD-3-Clause |
| lxml | Reads and writes presentation XML | BSD-3-Clause |
| olefile | Reads the container of legacy .ppt files | BSD-2-Clause |
| markdown-it-py, mdit-py-plugins, mdurl | Parse Markdown | MIT |
| ziamath, ziafont, latex2mathml | Draw LaTeX formulas, using the bundled STIX Two Math font | MIT (font: SIL OFL) |
| Pygments | Colours code in Markdown | BSD-2-Clause |
| Python 3.13 | Runtime | PSF-2.0 |

The ARM64 package uses the official ARM64 build of each component. The one version difference is cryptography: ARM64 uses 46.0.3, the last release published for Windows ARM64, and x64 uses 50.0.2. On ARM64, the C++ runtime for the office engine comes from the Qt package, because the LibreOffice installer carries only its x86 and x64 copies.

Source for LibreOffice is at https://www.libreoffice.org/download/source-code/, and for Qt at https://code.qt.io. To use a different build of Qt under the LGPL, replace the Qt DLLs in `_internal/PySide6`.
