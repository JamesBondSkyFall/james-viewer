"""Measure the packaged exe without any keyboard or mouse input, so it also works on a locked PC.

  exe_timing.py <folder holding James Viewer.exe> <data folder> <results.json> <files...>

For each file: the first open (time to the first slides, and to the whole deck), then a reopen in a
new process, then the memory in use.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of(pid: int) -> list[tuple[int, str]]:
    found = []

    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(400)
            user32.GetWindowTextW(hwnd, buffer, 400)
            found.append((hwnd, buffer.value))
        return True

    user32.EnumWindows(EnumProc(visit), 0)
    return found


def wait(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def processes(folder: Path) -> list[dict]:
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '" + str(folder) + "*' } | "
        "Select-Object Name,WorkingSetSize | ConvertTo-Json -Compress"
    )
    output = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout.strip()
    if not output:
        return []
    data = json.loads(output)
    return data if isinstance(data, list) else [data]


def memory(folder: Path) -> dict:
    totals: dict[str, int] = {}
    for item in processes(folder):
        totals[item["Name"]] = totals.get(item["Name"], 0) + round(item["WorkingSetSize"] / 1e6)
    return totals


def launch(exe: Path, data: Path, *files: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("JAMES_VIEWER", "PYTHON", "QT_"))}
    env["JAMES_VIEWER_DATA_DIR"] = str(data)
    return subprocess.Popen([str(exe), *files], env=env, cwd=tempfile.gettempdir())


def close(process: subprocess.Popen) -> None:
    for hwnd, _title in windows_of(process.pid):
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
    try:
        process.wait(20)
    except subprocess.TimeoutExpired:
        process.kill()


def chunks(data: Path) -> list[Path]:
    return list((data / "cache").rglob("chunk-0000.pdf")) + list((data / "cache").rglob("document.pdf"))


def manifests(data: Path) -> list[Path]:
    return list((data / "cache").rglob("manifest.json"))


def main() -> None:
    folder, data, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    exe = folder / "James Viewer.exe"
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    results: dict = {"files": {}}

    starts = []
    for _ in range(3):
        started = time.perf_counter()
        process = launch(exe, data)
        wait(lambda: bool(windows_of(process.pid)), 30)
        starts.append(round(time.perf_counter() - started, 2))
        time.sleep(1.0)
        results["memory_mb_idle"] = memory(folder)
        close(process)
    results["startup_seconds"] = starts
    print("startup", starts, "memory idle", results["memory_mb_idle"], flush=True)

    for value in sys.argv[4:]:
        path = Path(value)
        entry: dict = {}
        is_office = path.suffix.lower() in (".ppt", ".pptx")
        for attempt in ("first", "reopen"):
            known_chunks, known_manifests = set(chunks(data)), set(manifests(data))
            started = time.perf_counter()
            process = launch(exe, data, str(path))
            opened = wait(lambda: any(path.name in title for _hwnd, title in windows_of(process.pid)), 60)
            entry[f"{attempt}_window_seconds"] = round(time.perf_counter() - started, 2)
            if is_office and attempt == "first":
                wait(lambda: set(chunks(data)) - known_chunks, 180)
                entry["first_slides_seconds"] = round(time.perf_counter() - started, 2)
                wait(lambda: set(manifests(data)) - known_manifests, 300)
                entry["whole_deck_seconds"] = round(time.perf_counter() - started, 2)
            time.sleep(1.5)
            current = memory(folder)
            entry[f"{attempt}_memory_mb"] = current
            entry[f"{attempt}_office_engine_started"] = any("soffice" in name for name in current)
            entry[f"{attempt}_opened"] = opened
            close(process)
            time.sleep(0.5)
        results["files"][path.name] = entry
        print(path.name, json.dumps(entry, ensure_ascii=False), flush=True)
    left = processes(folder)
    results["processes_left_after_close"] = [item["Name"] for item in left]
    print("left running:", results["processes_left_after_close"], flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
