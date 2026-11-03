"""Build the Windows ARM64 package on an x64 PC, using only official ARM64 files.

PyInstaller cannot build for another processor, but only part of what it produces depends on one.
Compiled Python and data are the same everywhere. So this script:

1. runs PyInstaller under x64 Python, with the same package versions as the ARM64 wheels,
2. replaces every native file with its twin from the wheels and the embeddable Python,
3. puts the same embedded archive behind PyInstaller's own ARM64 start-up program,
4. adds the ARM64 office bundle, then checks every native file's processor and imports.

This PC cannot run the result. `--control` therefore runs the same steps with x64 parts, in
.build/arm64-control, and the output of that can be run and tested here.

  python tools/build_arm64.py [--control]

Expects in the parts folder (.build/arm64 or .build/arm64-control):
  wheels-x/   the wheels of every native package, unzipped together
  python/     the official embeddable Python, unzipped
  venv/       an x64 environment with the same package versions
  office-src/ the official LibreOffice MSI, unpacked (ARM64 only; control uses .build/office)
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import pefile

from build_app import QT_REMOVE, VERSION, size_of
from build_office import assemble, delete

CONTROL = "--control" in sys.argv
PARTS = ROOT / ".build" / ("arm64-control" if CONTROL else "arm64")
WHEELS = PARTS / "wheels-x"
PYTHON = PARTS / "python"
VENV_PYTHON = PARTS / "venv" / "Scripts" / "python.exe"
MACHINE = 0x8664 if CONTROL else 0xAA64
OUR_TAG, THEIR_TAG = "win_amd64", ("win_amd64" if CONTROL else "win_arm64")
BOOTLOADER = WHEELS / "PyInstaller" / "bootloader" / ("Windows-64bit-intel" if CONTROL else "Windows-64bit-arm") / "runw.exe"
DIST = PARTS / "package" if CONTROL else ROOT / "dist" / VERSION / "arm64"
ZIP_NAME = f"James-Viewer-{VERSION}-win-{'control' if CONTROL else 'arm64'}-portable.zip"
# The Visual C++ runtime is not guaranteed on Windows, so it has to travel with the app.
MUST_TRAVEL = ("vcruntime", "msvcp", "concrt", "vccorlib", "vcomp", "vcamp")
SYSTEM32 = Path(os.environ["SystemRoot"]) / "System32"
# Imported only by officebean.dll and scfiltlo.dll, which a slide conversion never loads.
DEAD_IMPORTS = {"jawt.dll", "sclo.dll"}


def is_native(path: Path) -> bool:
    if path.suffix.lower() not in (".dll", ".pyd", ".exe", ".bin", ".com"):
        return False
    with path.open("rb") as stream:
        return stream.read(2) == b"MZ"


def is_unused_helper(path: Path) -> bool:
    """Files LibreOffice ships that a slide conversion never loads, some built for other processors."""
    name, folders = path.name.lower(), [part.lower() for part in path.parts]
    if name.startswith(("cli_", "policy.1.0.cli_")) or name == "twain32shim.exe":
        return True
    return any(part.startswith("python-core") for part in folders) and ("setuptools" in folders or "pip" in folders)


def machine_of(path: Path) -> int:
    with path.open("rb") as stream:
        head = stream.read(4096)
    offset = struct.unpack_from("<I", head, 0x3C)[0]
    return struct.unpack_from("<H", head, offset + 4)[0]


def imports_of(path: Path) -> list[str]:
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
    ])
    names = [entry.dll.decode("ascii", "replace") for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
    delayed = [entry.dll.decode("ascii", "replace") for entry in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
    pe.close()
    return names + delayed


def index_natives(*folders: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for folder in folders:
        for item in folder.rglob("*"):
            if item.is_file() and ".dist-info" not in str(item) and "bootloader" not in str(item) and is_native(item):
                found.setdefault(item.name.lower(), item)
    return found


def align_python_sources() -> None:
    """Module lists can differ between the wheels, so PyInstaller must see the target's Python files."""
    site = PARTS / "venv" / "Lib" / "site-packages"
    for source in WHEELS.rglob("*.py"):
        relative = source.relative_to(WHEELS)
        if relative.parts[0] != "PyInstaller" and (site / relative).exists() and (site / relative).read_bytes() != source.read_bytes():
            shutil.copyfile(source, site / relative)
            print(f"aligned {relative}", flush=True)


def run_pyinstaller() -> Path:
    environment = {**os.environ, "JAMES_VIEWER_ROOT": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    subprocess.run(
        [
            str(VENV_PYTHON), "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(PARTS / "pyi"), "--workpath", str(PARTS / "pyi-work"),
            str(ROOT / "tools" / "james_viewer.spec"),
        ],
        check=True, cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return PARTS / "pyi" / "James Viewer"


def swap_native_files(internal: Path) -> None:
    natives = index_natives(WHEELS, PYTHON)
    missing: list[str] = []
    for folder in [item for item in internal.iterdir() if item.is_dir() and item.name.endswith(".libs")]:
        shutil.rmtree(folder)
        shutil.copytree(WHEELS / folder.name, folder)
    replaced = 0
    for item in sorted(internal.rglob("*")):
        if not item.is_file() or not is_native(item) or item.parent.name.endswith(".libs"):
            continue
        name = item.name.lower()
        if name.startswith(("api-ms-win-", "ext-ms-win-")) or name == "ucrtbase.dll":
            item.unlink()  # part of Windows itself
            continue
        if not CONTROL and name in ("libcrypto-3.dll", "libssl-3.dll"):
            item.unlink()  # ARM64 Python has them as libcrypto-3-arm64.dll, added below as dependencies
            continue
        relative = item.relative_to(internal)
        target = item.with_name(item.name.replace(OUR_TAG, THEIR_TAG))
        twin = WHEELS / relative.with_name(target.name)
        twin = twin if twin.is_file() else natives.get(target.name.lower())
        if twin is None:
            missing.append(str(relative))
            continue
        if name.startswith(MUST_TRAVEL) and machine_of(twin) != MACHINE:
            item.unlink()  # such as vcruntime140_1.dll, which ARM64 code does not use. verify() catches any need.
            continue
        item.unlink()
        shutil.copyfile(twin, target)
        replaced += 1
    print(f"replaced {replaced} native files", flush=True)
    if missing:
        raise SystemExit(f"No {THEIR_TAG} twin for: {missing}")
    add_missing_dependencies(internal, natives)


def add_missing_dependencies(internal: Path, natives: dict[str, Path]) -> None:
    """The twins can need files x64 did not, such as libcrypto-3-arm64.dll."""
    while True:
        present = {item.name.lower() for item in internal.rglob("*") if item.is_file()}
        needed = {
            dependency.lower()
            for item in internal.rglob("*")
            if item.is_file() and is_native(item)
            for dependency in imports_of(item)
        }
        fresh = sorted(name for name in needed - present if name in natives)
        if not fresh:
            return
        for name in fresh:
            shutil.copyfile(natives[name], internal / natives[name].name)
            print(f"added dependency {natives[name].name}", flush=True)


def build_exe(x64_exe: Path, out: Path) -> None:
    from PyInstaller import config
    from PyInstaller.utils.win32 import icon, versioninfo, winmanifest, winresource, winutils

    config.CONF["workpath"] = str(PARTS / "pyi-work")
    manifest = winmanifest.read_manifest_from_executable(str(x64_exe))
    shutil.copyfile(BOOTLOADER, out)
    os.chmod(out, 0o755)
    winresource.remove_all_resources(str(out))
    icon.CopyIcons(str(out), str(ROOT / ".build" / "icon" / "james_viewer.ico"))
    versioninfo.write_version_info_to_executable(str(out), versioninfo.load_version_info_from_text_file(str(ROOT / "tools" / "version_info.txt")))
    winmanifest.write_manifest_to_executable(str(out), manifest)
    package = PARTS / "pyi-work" / "james_viewer" / "James Viewer.pkg"
    with out.open("ab") as output, package.open("rb") as source:
        shutil.copyfileobj(source, output)
    winutils.set_exe_build_timestamp(str(out), int(x64_exe.stat().st_mtime))
    winutils.update_exe_pe_checksum(str(out))


def build_office_bundle() -> Path:
    x64 = ROOT / ".build" / "office"
    if CONTROL:
        return x64
    target = PARTS / "office"
    shutil.rmtree(target, ignore_errors=True)
    assemble(PARTS / "office-src", target)
    for relative in json.loads((ROOT / "tools" / "office_removable.json").read_text(encoding="utf-8")):
        delete(target / relative)
    for installer in target.glob("*.msi"):
        installer.unlink()
    # The ARM64 installer carries only the x86 and x64 C++ runtime, and assemble() has just copied the
    # x64 one. Official ARM64 copies come with the Qt wheel and the embeddable Python.
    natives = index_natives(WHEELS, PYTHON)
    for runtime in [item for item in (target / "program").glob("*.dll") if item.name.lower().startswith(MUST_TRAVEL)]:
        runtime.unlink()
        twin = natives.get(runtime.name.lower())
        if twin is not None and machine_of(twin) == MACHINE:
            shutil.copyfile(twin, runtime)
    # The two builds have one build id, and a finished profile holds no native code.
    shutil.copytree(x64 / "james-profile", target / "james-profile")
    return target


def verify(app: Path) -> int:
    problems: list[str] = []
    every = [item for item in app.rglob("*") if item.is_file()]
    natives = [item for item in every if is_native(item) and not is_unused_helper(item)]
    wrong = [item for item in natives if machine_of(item) != MACHINE]
    for item in wrong:
        problems.append(f"wrong processor {machine_of(item):#x}: {item.relative_to(app)}")
    unresolved: dict[str, set[str]] = {}
    from_windows: set[str] = set()
    for root in (app / "_internal", app / "office"):
        present = {item.name.lower() for item in root.rglob("*") if item.is_file()}
        for item in (entry for entry in natives if entry.is_relative_to(root)):
            for dependency in imports_of(item):
                name = dependency.lower()
                if name in present or name in DEAD_IMPORTS or name.startswith(("api-ms-win-", "ext-ms-")):
                    continue
                if name.startswith(MUST_TRAVEL) or not (SYSTEM32 / name).exists():
                    unresolved.setdefault(name, set()).add(str(item.relative_to(app)))
                else:
                    from_windows.add(name)
    for name, users in sorted(unresolved.items()):
        problems.append(f"unresolved import {name}, used by {sorted(users)[:3]}")
    print(f"needed from Windows itself: {sorted(from_windows)}", flush=True)

    exe = app / "James Viewer.exe"
    pe = pefile.PE(str(exe), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    kinds = {entry.id for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
    pe.close()
    if not {3, 14, 16, 24} <= kinds:  # icon, icon group, version, manifest
        problems.append(f"the exe is missing resources: has {sorted(kinds)}")

    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    if not any(name.startswith("pyi_rth") or name == "__main__" for name in archive.toc):
        problems.append("the exe's archive has no start-up scripts")

    print(f"{len(every)} files, {len(natives)} native, all {MACHINE:#x}: {not wrong}", flush=True)
    print(f"archive entries in exe: {len(archive.toc)}", flush=True)
    for problem in problems:
        print("PROBLEM:", problem, flush=True)
    return len(problems)


def main() -> None:
    align_python_sources()
    built = run_pyinstaller()
    shutil.rmtree(DIST, ignore_errors=True)
    app = DIST / "James Viewer"
    shutil.copytree(built, app)
    internal = app / "_internal"
    for pattern in QT_REMOVE:
        for match in (internal / "PySide6").glob(pattern):
            if match.is_dir():
                shutil.rmtree(match)
            else:
                match.unlink()
    swap_native_files(internal)
    build_exe(built / "James Viewer.exe", app / "James Viewer.exe")
    shutil.copytree(build_office_bundle(), app / "office")
    shutil.copyfile(ROOT / "README.md", app / "README.md")
    licenses = app / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs" / "THIRD_PARTY.md", licenses / "THIRD_PARTY.md")
    shutil.copyfile(ROOT / "tests" / "fixtures" / "rich.pptx", licenses / "self-check.pptx")
    shutil.copyfile(ROOT / "tools" / "Self-check.cmd", app / "Self-check.cmd")
    print(f"app: {size_of(app) / 1e6:.0f} MB", flush=True)
    problems = verify(app)
    if problems:
        raise SystemExit(f"{problems} problems, no zip made.")

    archive = DIST.parent / ZIP_NAME
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for item in sorted(app.rglob("*")):
            if item.is_file():
                output.write(item, Path("James Viewer") / item.relative_to(app))
    with zipfile.ZipFile(archive) as check:
        assert check.testzip() is None
    print(f"zip: {archive.stat().st_size / 1e6:.0f} MB at {archive}", flush=True)


if __name__ == "__main__":
    main()
