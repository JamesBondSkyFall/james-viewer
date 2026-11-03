"""Build the trimmed office bundle from an unpacked LibreOffice MSI.

Removals are safe because `--verify` renders a test pack with the result and compares
every page with the same pack rendered by the untrimmed copy.

  python tools/build_office.py <unpacked msi> <output folder> [--probe <candidates> <test pack...>]
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Whole folders and files no headless Impress conversion can need.
ALWAYS_REMOVE = [
    "help",
    "readmes",
    "System",
    "Fonts",
    "CREDITS.fodt",
    "update-settings.ini",
    "share/extensions",
    "share/gallery",
    "share/template",
    "share/wizards",
    "program/classes",
    "program/shlxthdl",
    "program/xpdfimport.exe",
    "share/xpdfimport",
]

# Libraries that may be removable. Each survives only if the test pack still renders identically.
CANDIDATES = [
    "presets",
    "share/autotext",
    "share/autocorr",
    "share/basic",
    "share/Scripts",
    "share/xslt",
    "share/firebird",
    "share/labels",
    "share/tipoftheday",
    "share/numbertext",
    "share/classification",
    "share/toolbarmode",
    "share/calc",
    "share/dtd",
    "share/fingerprint",
    "share/config/images_*",
    "share/config/wizard",
    "share/registry/res",
    "share/registry/writer.xcd",
    "share/registry/calc.xcd",
    "share/registry/base.xcd",
    "share/registry/reportbuilder.xcd",
    "share/registry/librelogo.xcd",
    "share/registry/xsltfilter.xcd",
    "share/registry/oo-ldap.xcd.sample",
    "program/resource/*",
    "program/soffice_safe.exe",
    "program/sbase.exe",
    "program/scalc.exe",
    "program/sdraw.exe",
    "program/smath.exe",
    "program/swriter.exe",
    "program/simpress.exe",
    "program/quickstart.exe",
    "program/unoinfo.exe",
    "program/ui-previewer.exe",
    "program/senddoc.exe",
    "program/python-core-*/lib/test",
    "program/python-core-*/lib/tkinter",
    "program/python-core-*/lib/idlelib",
    "program/python-core-*/lib/ensurepip",
    "program/python-core-*/lib/unittest",
    "program/python-core-*/lib/pydoc_data",
    "program/python-core-*/lib/lib2to3",
    "program/python-core-*/lib/venv",
    "program/python-core-*/lib/asyncio",
    "program/python-core-*/lib/email",
    "program/python-core-*/lib/http",
    "program/python-core-*/lib/xml",
    "program/python-core-*/lib/multiprocessing",
    "program/python-core-*/lib/urllib",
    "program/python-core-*/lib/wsgiref",
    "program/python-core-*/lib/html",
    "program/python-core-*/lib/sqlite3",
    "program/python-core-*/lib/curses",
    "program/python-core-*/lib/dbm",
    "program/python-core-*/lib/logging",
    "program/python-core-*/lib/concurrent",
    "program/python-core-*/lib/ctypes",
    "program/python-core-*/lib/tomllib",
    "program/python-core-*/lib/zoneinfo",
    "program/python-core-*/lib/site-packages",
    "program/python-core-*/lib/__pycache__",
    "program/python-core-*/lib/**/__pycache__",
    "program/sclo.dll", "program/swlo.dll", "program/scfiltlo.dll", "program/mswordlo.dll",
    "program/sw_writerfilterlo.dll", "program/swuilo.dll", "program/scuilo.dll", "program/sduilo.dll",
    "program/swdlo.dll", "program/scdlo.dll", "program/vbaobjlo.dll", "program/vbaswobjlo.dll",
    "program/pcrlo.dll", "program/mwaw.dll", "program/etonyek.dll", "program/wps.dll",
    "program/staroffice.dll", "program/odfgen.dll", "program/Engine12.dll", "program/ifbclient.dll",
    "program/CoinMP.dll", "program/clucene.dll", "program/orcus.dll", "program/orcus-parser.dll",
    "program/skialo.dll", "program/epoxy.dll", "program/pdfiumlo.dll", "program/libcrypto-3.dll",
    "program/libssl-3.dll", "program/firebird_sdbclo.dll", "program/dbalo.dll", "program/dbulo.dll",
    "program/dbaxmllo.dll", "program/analysislo.dll", "program/datelo.dll", "program/solverlo.dll",
    "program/wpftwriterlo.dll", "program/wpftcalclo.dll", "program/wpftimpresslo.dll", "program/wpftdrawlo.dll",
    "program/hwplo.dll", "program/lwpftlo.dll", "program/t602filterlo.dll", "program/writerperfectlo.dll",
    "program/visio*.dll", "program/cdr*.dll", "program/freehand*.dll", "program/mspub*.dll",
    "program/pagemaker*.dll", "program/qxp*.dll", "program/zmf*.dll", "program/abw*.dll",
    "program/e-book*.dll", "program/ebook*.dll", "program/epubgen*.dll", "program/lpsolve*.dll",
    "program/smplug*.dll", "program/sdbc*.dll", "program/mysql*.dll", "program/postgresql*.dll",
    "program/gie*.dll", "program/ldapbe2lo.dll", "program/updatefeedlo.dll", "program/updatecheckuilo.dll",
    "program/updchklo.dll", "program/online_updatelo.dll", "program/libcurl.dll", "program/ucpdav1.dll",
    "program/ucpftp1.dll", "program/ucpcmis1.dll", "program/ucphier1.dll", "program/ucpgio1.dll",
    "program/nss*.dll", "program/smime*.dll", "program/ssl*.dll", "program/plc4.dll", "program/plds4.dll",
    "program/freebl3.dll", "program/softokn3.dll", "program/sqlite3.dll", "program/nssdbm3.dll",
    "program/textconv_dict.dll", "program/testtool*.dll", "program/wininetbe1lo.dll", "program/cmdmail*.dll",
    "program/smtp*.dll", "program/gpgme*.dll", "program/libassuan*.dll", "program/libgpg*.dll",
    "program/localedata_others.dll", "program/oox_vba*.dll", "program/ooofilt*.dll", "program/ooxml*.dll",
    "program/propertyhandler*.dll", "program/shsvcs*.dll", "program/inprocserv.dll", "program/so_activex*.dll",
    "program/spsupp*.dll", "program/twain*.dll", "program/wiaproxy*.dll", "program/scn*.dll",
    "share/config/soffice.cfg/modules/swriter", "share/config/soffice.cfg/modules/scalc",
    "share/config/soffice.cfg/modules/sglobal", "share/config/soffice.cfg/modules/sweb",
    "share/config/soffice.cfg/modules/sabpilot", "share/config/soffice.cfg/modules/dbapp",
    "share/config/soffice.cfg/modules/dbbrowser", "share/config/soffice.cfg/modules/dbquery",
    "share/config/soffice.cfg/modules/dbrelation", "share/config/soffice.cfg/modules/dbreport",
    "share/config/soffice.cfg/modules/dbtable", "share/config/soffice.cfg/modules/dbtdata",
    "share/config/soffice.cfg/modules/BasicIDE", "share/config/soffice.cfg/modules/StartModule",
    "share/config/soffice.cfg/modules/swform", "share/config/soffice.cfg/modules/swreport",
    "share/config/soffice.cfg/modules/swxform", "share/config/soffice.cfg/modules/sbibliography",
    "share/config/soffice.cfg/modules/schart", "share/config/soffice.cfg/svt", "share/config/soffice.cfg/xmlsec",
    "share/config/soffice.cfg/cui", "share/config/soffice.cfg/sfx", "share/config/soffice.cfg/fps",
]


def expand(root: Path, patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        found.extend(root.glob(pattern))
    return found


def delete(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def assemble(source: Path, output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(source, output)
    for dll in (source / "System64").glob("*.dll"):
        shutil.copy2(dll, output / "program" / dll.name)
    delete(output / "System64")
    fonts = output / "share" / "fonts" / "truetype"
    fonts.mkdir(parents=True, exist_ok=True)
    for font in (source / "Fonts").glob("opens___*"):
        shutil.copy2(font, fonts / font.name)
    for path in expand(output, ALWAYS_REMOVE):
        delete(path)
    (output / "program" / "resource").mkdir(exist_ok=True)


def render_pack(office: Path, pack: list[Path], out: Path) -> dict[str, list[str]] | None:
    """Render every file in the pack. Returns a page-image fingerprint per file, or None on failure."""
    from james_viewer.core.office_engine import OfficeEngine
    import pypdfium2

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    os.environ["JAMES_VIEWER_DATA_DIR"] = str(out / "data")
    engine = OfficeEngine(office)
    result: dict[str, list[str]] = {}
    try:
        for item in pack:
            pdf = out / (item.stem + ".pdf")
            reply = engine.to_pdf(item, pdf)
            document = pypdfium2.PdfDocument(str(pdf))
            signatures = []
            for index in range(len(document)):
                page = document[index]
                image = page.render(scale=0.5).to_pil().convert("L").resize((96, 54))
                signatures.append(image.tobytes().hex())
            document.close()
            result[item.name] = signatures
            if reply.get("pages") != len(signatures):
                return None
    except Exception as exc:
        print("   render failed:", str(exc)[:160], flush=True)
        return None
    finally:
        engine.close()
    return result


def same(a: dict[str, list[str]], b: dict[str, list[str]], tolerance: float = 0.6) -> bool:
    """True when every page matches. Tolerates sub-pixel noise, not missing content."""
    if a.keys() != b.keys():
        return False
    for name in a:
        if len(a[name]) != len(b[name]):
            return False
        for left, right in zip(a[name], b[name]):
            x, y = bytes.fromhex(left), bytes.fromhex(right)
            if sum(abs(p - q) for p, q in zip(x, y)) / len(x) > tolerance:
                print(f"   page differs in {name}", flush=True)
                return False
    return True


def probe(output: Path, pack: list[Path], baseline: dict, work: Path, parked: Path) -> list[str]:
    """Group-test the candidates and return the ones that can be removed."""
    parked.mkdir(parents=True, exist_ok=True)
    removable: list[str] = []
    found = {p for pattern in CANDIDATES for p in output.glob(pattern)}
    # A folder and something inside it can both match. Keep only the outermost, so parking
    # the folder cannot make a later candidate vanish.
    entries = sorted(p for p in found if not any(parent in found for parent in p.parents))
    print(f"candidates present: {len(entries)}, {sum(size_of(p) for p in entries) / 1e6:.0f} MB", flush=True)

    def park(paths: list[Path]) -> list[tuple[Path, Path]]:
        moves = []
        for index, path in enumerate(paths):
            target = parked / f"{time.time_ns()}_{index}_{path.name}"
            shutil.move(str(path), target)
            moves.append((path, target))
        return moves

    def restore(moves: list[tuple[Path, Path]]) -> None:
        for original, target in moves:
            shutil.move(str(target), original)

    def attempt(group: list[Path]) -> None:
        if not group:
            return
        moves = park(group)
        started = time.time()
        rendered = render_pack(output, pack, work)
        good = rendered is not None and same(baseline, rendered)
        print(f"  {'ok ' if good else 'BAD'} removing {len(group):3d} items ({time.time() - started:.0f}s): {group[0].name}...", flush=True)
        if good:
            removable.extend(str(p.relative_to(output)).replace("\\", "/") for p in group)
            return
        restore(moves)
        if len(group) > 1:
            middle = len(group) // 2
            attempt(group[:middle])
            attempt(group[middle:])

    attempt(entries)
    return removable


def add_profile_seed(output: Path) -> None:
    """Include a profile that has finished its first run, which is the crash-prone part."""
    from james_viewer.core.office_engine import create_profile_seed

    os.environ.setdefault("JAMES_VIEWER_DATA_DIR", str(output.parent / "office-seed-data"))
    seed = create_profile_seed(output, ROOT / "tests" / "fixtures" / "rich.pptx")
    print(f"profile seed: {size_of(seed) / 1e6:.1f} MB", flush=True)


def main() -> None:
    source, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    assemble_only = "--probe" not in sys.argv
    manifest = ROOT / "tools" / "office_removable.json"
    if assemble_only:
        assemble(source, output)
        if manifest.exists():
            for relative in json.loads(manifest.read_text(encoding="utf-8")):
                delete(output / relative)
        add_profile_seed(output)
        print(f"bundle: {size_of(output) / 1e6:.0f} MB at {output}")
        return

    pack = [Path(p).resolve() for p in sys.argv[sys.argv.index("--probe") + 1 :]]
    work = output.parent / "office-probe"
    print("baseline with the full copy...", flush=True)
    baseline = render_pack(source, pack, work)
    assert baseline, "the untrimmed office failed to render the test pack"
    print({k: len(v) for k, v in baseline.items()}, flush=True)
    assemble(source, output)
    print(f"after fixed removals: {size_of(output) / 1e6:.0f} MB", flush=True)
    first = render_pack(output, pack, work)
    if first is None or not same(baseline, first):
        raise SystemExit("The fixed removal list broke rendering; narrow ALWAYS_REMOVE before probing.")
    removable = probe(output, pack, baseline, work, output.parent / "office-parked")
    manifest.write_text(json.dumps(sorted(removable), indent=1), encoding="utf-8")
    shutil.rmtree(output.parent / "office-parked", ignore_errors=True)
    add_profile_seed(output)
    print(f"final: {size_of(output) / 1e6:.0f} MB, removable {len(removable)}", flush=True)


if __name__ == "__main__":
    main()
