"""Test the packaged James Viewer.exe from outside, the way Windows sees it.

  blackbox.py <folder holding James Viewer.exe> <results folder> <deck.pptx> <doc.pdf> <drop.pdf> [notes.md] [long.pdf]

Real key presses are sent only after confirming James Viewer has the foreground, so they cannot land in
another application. Real mouse input is used once, for the drag-and-drop check.
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

from PIL import ImageChops, ImageGrab, ImageStat

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetForegroundWindow.restype = wintypes.HWND
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
VK_F11, VK_ESCAPE, VK_RIGHT, VK_LEFT, VK_MENU, VK_CONTROL, VK_L, VK_B = 0x7A, 0x1B, 0x27, 0x25, 0x12, 0x11, 0x4C, 0x42
results: dict = {}


class CursorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", ctypes.c_void_p), ("pos", wintypes.POINT)]


def windows_of(pid: int) -> list[int]:
    found = []

    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(visit), 0)
    return found


def title_of(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(400)
    user32.GetWindowTextW(hwnd, buffer, 400)
    return buffer.value


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def wait(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def bring_forward(hwnd: int) -> bool:
    """Windows blocks background processes from taking the foreground, so join the input queues."""
    me = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(hwnd, None)
    for _ in range(8):
        holder = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        user32.AttachThreadInput(me, holder, True)
        user32.AttachThreadInput(target, holder, True)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 2, 0)
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SwitchToThisWindow(hwnd, True)
        user32.AttachThreadInput(target, holder, False)
        user32.AttachThreadInput(me, holder, False)
        if wait(lambda: user32.GetForegroundWindow() == hwnd, 0.8):
            return True
    return False


def press(key: int, pid: int) -> None:
    foreground = user32.GetForegroundWindow()
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(owner))
    if owner.value != pid:
        raise RuntimeError(f"refusing to send a key: the foreground window is not James Viewer ({title_of(foreground)!r})")
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, 2, 0)


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def grab(path: Path | None = None):
    image = ImageGrab.grab(all_screens=False)
    if path:
        image.convert("RGB").save(path)
    return image.convert("RGB")


def difference(a, b) -> float:
    return ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0]


def folder_processes(folder: Path) -> list[dict]:
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '" + str(folder) + "*' } | "
        "Select-Object ProcessId,Name,WorkingSetSize | ConvertTo-Json -Compress"
    )
    output = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout.strip()
    if not output:
        return []
    data = json.loads(output)
    return data if isinstance(data, list) else [data]


def tcp_connections(pids: list[int]) -> int:
    if not pids:
        return 0
    script = "@(Get-NetTCPConnection -OwningProcess " + ",".join(map(str, pids)) + " -ErrorAction SilentlyContinue).Count"
    output = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout.strip()
    return int(output or 0)


def check(name: str, passed: bool, detail="") -> None:
    results[name] = {"passed": bool(passed), "detail": detail}
    print(("PASS " if passed else "FAIL ") + name + (f"  [{detail}]" if detail != "" else ""), flush=True)


def launch(exe: Path, data: Path, *files: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("JAMES_VIEWER", "PYTHON", "QT_"))}
    env["JAMES_VIEWER_DATA_DIR"] = str(data)
    return subprocess.Popen([str(exe), *files], env=env, cwd=str(Path(tempfile.gettempdir())))


def real_drag(pid: int, hwnd: int, drop_file: str, helper_python: str, data: Path) -> bool:
    """Start a real OLE drag from a helper window and release it over James Viewer with real mouse input."""
    script = Path(__file__).with_name("drag_source.py")
    ready = data / "drag-ready.json"
    helper = subprocess.Popen([helper_python, str(script), drop_file, str(ready)])
    try:
        if not wait(ready.exists, 25):
            return False
        time.sleep(0.4)
        source = json.loads(ready.read_text(encoding="utf-8"))
        left, top, right, bottom = rect_of(hwnd)
        target = ((left + right) // 2, (top + bottom) // 2)
        keep = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(keep))
        user32.SetCursorPos(source["x"], source["y"])
        time.sleep(0.25)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.2)
        for step in range(1, 26):
            user32.SetCursorPos(source["x"] + (target[0] - source["x"]) * step // 25, source["y"] + (target[1] - source["y"]) * step // 25)
            time.sleep(0.03)
        time.sleep(0.3)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.2)
        user32.SetCursorPos(keep.x, keep.y)
        return True
    finally:
        helper.terminate()


def wheel(notches: int, with_ctrl: bool) -> None:
    """Real wheel input at the pointer. The caller has already checked the foreground window."""
    if with_ctrl:
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.05)
    user32.mouse_event(0x0800, 0, 0, 120 * notches, 0)
    time.sleep(0.05)
    if with_ctrl:
        user32.keybd_event(VK_CONTROL, 0, 2, 0)


def ctrl_key(key: int, pid: int) -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.03)
    press(key, pid)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)


def window_image(hwnd: int, path: Path | None = None):
    """Only this window, so that another app changing on screen cannot affect a comparison."""
    image = grab().crop(rect_of(hwnd))
    if path:
        image.save(path)
    return image


def client_corner(hwnd: int) -> tuple[int, int]:
    point = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y


def cpu_seconds(process: subprocess.Popen) -> float:
    created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
    kernel32.GetProcessTimes(wintypes.HANDLE(int(process._handle)), ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user))
    return sum((value.dwHighDateTime << 32 | value.dwLowDateTime) / 1e7 for value in (kernel, user))


def markdown_place_checks(pid: int, hwnd: int, out: Path) -> None:
    """The reader's place survives hiding panels and going through full screen, with real keys."""
    start = window_image(hwnd, out / "20-md-before-panels.png")
    ctrl_key(VK_L, pid)
    time.sleep(0.8)
    hidden = window_image(hwnd, out / "21-md-layout-hidden.png")
    check("real Ctrl + L hides the layout", difference(start, hidden) > 1.0, round(difference(start, hidden), 2))
    ctrl_key(VK_L, pid)
    time.sleep(0.8)
    back = window_image(hwnd)
    check("hiding and showing the layout keeps the place", difference(start, back) < 1.0, round(difference(start, back), 3))

    ctrl_key(VK_B, pid)
    time.sleep(0.8)
    no_panel = grab(out / "22-md-control-panel-hidden.png")
    x, y = client_corner(hwnd)
    corner = no_panel.crop((x, y, x + 150, y + 110))
    green = sum(1 for red, green_value, blue in corner.getdata() if green_value > red + 15 and green_value > blue + 10)
    check("the app icon stays at the top left without the control panel", green > 200, f"{green} green pixels")
    ctrl_key(VK_B, pid)
    time.sleep(0.8)
    back = window_image(hwnd)
    check("hiding and showing the control panel keeps the place", difference(start, back) < 1.0, round(difference(start, back), 3))

    press(VK_F11, pid)
    time.sleep(1.5)
    press(VK_ESCAPE, pid)
    time.sleep(1.0)
    back = window_image(hwnd)
    check("entering and leaving full screen keeps the place", difference(start, back) < 1.0, round(difference(start, back), 3))
    press(VK_F11, pid)
    time.sleep(1.5)
    wheel(-8, with_ctrl=False)
    time.sleep(0.6)
    press(VK_ESCAPE, pid)
    time.sleep(1.0)
    moved = window_image(hwnd, out / "23-md-after-full-screen-scroll.png")
    check("scrolling in full screen carries back to the window", difference(start, moved) > 1.0, round(difference(start, moved), 2))


def long_pdf_checks(exe: Path, data: Path, out: Path, pdf: str) -> None:
    """A long PDF once blinked for ever after paging through it. It must come to rest."""
    process = launch(exe, data, pdf)
    pid = process.pid
    opened = wait(lambda: any(Path(pdf).name in title_of(h) for h in windows_of(pid)), 40)
    check("the long PDF opens", opened)
    time.sleep(2.0)
    hwnd = next(h for h in windows_of(pid) if Path(pdf).name in title_of(h))
    if not bring_forward(hwnd):
        check("James Viewer reached the foreground for the long PDF", False)
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return
    keep = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(keep))
    left, top, right, bottom = rect_of(hwnd)
    user32.SetCursorPos((left + right) // 2 + 300, (top + bottom) // 2)
    time.sleep(0.2)
    for _ in range(200):
        press(VK_RIGHT, pid)
        time.sleep(0.02)
    for _ in range(80):
        press(VK_LEFT, pid)
        time.sleep(0.02)
    time.sleep(2.5)
    frames = [window_image(hwnd, out / "30-long-pdf-at-rest.png")]
    before = cpu_seconds(process)
    for _ in range(12):
        time.sleep(0.25)
        frames.append(window_image(hwnd))
    used = cpu_seconds(process) - before
    worst = max(difference(frames[0], frame) for frame in frames)
    check("the long PDF stops changing after paging", worst < 0.01, round(worst, 4))
    check("the long PDF uses almost no CPU at rest", used < 0.3, f"{used:.2f}s over 3s")
    darkest = frames[0].convert("L").crop((frames[0].width // 2, frames[0].height // 4, frames[0].width * 9 // 10, frames[0].height * 3 // 4)).getextrema()[0]
    check("the page on screen has content, not a blank sheet", darkest < 120, darkest)
    user32.SetCursorPos(keep.x, keep.y)
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
    process.wait(20)


def markdown_checks(exe: Path, data: Path, out: Path, markdown: str, folder: Path) -> None:
    import hashlib

    before = hashlib.sha256(Path(markdown).read_bytes()).hexdigest()
    started = time.perf_counter()
    process = launch(exe, data, markdown)
    pid = process.pid
    opened = wait(lambda: any(Path(markdown).name in title_of(h) for h in windows_of(pid)), 40)
    results["markdown_open_seconds"] = round(time.perf_counter() - started, 2)
    check("Markdown opens", opened, results["markdown_open_seconds"])
    time.sleep(2.0)
    check("Markdown does not start the office engine", not any("soffice" in p["Name"] for p in folder_processes(folder)))
    hwnd = next(h for h in windows_of(pid) if Path(markdown).name in title_of(h))
    if not bring_forward(hwnd):
        check("James Viewer reached the foreground for Markdown", False)
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return
    left, top, right, bottom = rect_of(hwnd)
    keep = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(keep))
    user32.SetCursorPos((left + right) // 2 + 200, (top + bottom) // 2)
    time.sleep(0.3)
    # Start partway down. At the very top, zooming out has nowhere to keep the reading position.
    for _ in range(3):
        wheel(-10, with_ctrl=False)
        time.sleep(0.2)
    time.sleep(0.5)
    start = grab(out / "10-md-100.png")
    wheel(4, with_ctrl=True)
    time.sleep(0.8)
    bigger = grab(out / "11-md-zoomed-in.png")
    check("real Ctrl + wheel zooms in", difference(start, bigger) > 1.0, round(difference(start, bigger), 2))
    wheel(-8, with_ctrl=True)
    time.sleep(0.8)
    smaller = grab(out / "12-md-zoomed-out.png")
    check("real Ctrl + wheel zooms out", difference(bigger, smaller) > 1.0, round(difference(bigger, smaller), 2))
    wheel(4, with_ctrl=True)
    time.sleep(0.8)
    back = grab()
    check("zooming back returns to the same view", difference(start, back) < 3.0, round(difference(start, back), 2))
    wheel(-6, with_ctrl=False)
    time.sleep(0.5)
    scrolled = grab(out / "13-md-scrolled.png")
    check("real plain wheel scrolls", difference(back, scrolled) > 1.0, round(difference(back, scrolled), 2))
    markdown_place_checks(pid, hwnd, out)

    width, height = screen_size()
    press(VK_F11, pid)
    time.sleep(1.5)
    foreground = user32.GetForegroundWindow()
    check("Markdown F11 covers the whole monitor", rect_of(foreground) == (0, 0, width, height), rect_of(foreground))
    check("Markdown F11 has no title bar or border", (user32.GetWindowLongW(foreground, -16) & 0x00C40000) == 0)
    full = grab(out / "14-md-full-screen.png")
    info = CursorInfo(cbSize=ctypes.sizeof(CursorInfo))
    user32.GetCursorInfo(ctypes.byref(info))
    check("cursor is hidden in Markdown full screen", info.flags == 0 or info.hCursor != ctypes.cast(user32.LoadCursorW(None, 32512), ctypes.c_void_p).value)
    wheel(3, with_ctrl=True)
    time.sleep(0.8)
    full_zoomed = grab(out / "15-md-full-screen-zoomed.png")
    check("real Ctrl + wheel zooms in full screen", difference(full, full_zoomed) > 1.0, round(difference(full, full_zoomed), 2))
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(0x4C, 0, 0, 0)
    user32.keybd_event(0x4C, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.6)
    layout_shown = grab(out / "16-md-full-screen-layout.png")
    check("Ctrl + L shows the layout in full screen", difference(full_zoomed, layout_shown) > 1.0, round(difference(full_zoomed, layout_shown), 2))
    press(VK_ESCAPE, pid)
    time.sleep(1.0)
    check("Esc leaves Markdown full screen", user32.GetForegroundWindow() == hwnd)
    grab(out / "17-md-after-esc.png")
    user32.SetCursorPos(keep.x, keep.y)
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
    process.wait(20)
    after = hashlib.sha256(Path(markdown).read_bytes()).hexdigest()
    check("the Markdown file is unchanged", before == after, after[:16])


def main() -> int:
    folder, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    deck, pdf, drop = [str(Path(p).resolve()) for p in sys.argv[3:6]]
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data"
    shutil.rmtree(data, ignore_errors=True)
    exe = folder / "James Viewer.exe"

    # Startup time and memory with nothing open, measured over three runs.
    startups = []
    for _ in range(3):
        started = time.perf_counter()
        process = launch(exe, data)
        wait(lambda: bool(windows_of(process.pid)), 30)
        startups.append(time.perf_counter() - started)
        time.sleep(0.8)
        user32.PostMessageW(windows_of(process.pid)[0], 0x0010, 0, 0)
        process.wait(15)
    check("startup to window", min(startups) < 3.0, f"best {min(startups):.2f}s, runs {[round(s, 2) for s in startups]}")

    # PDF: time to window with the document open.
    started = time.perf_counter()
    process = launch(exe, data, pdf)
    pid = process.pid
    wait(lambda: any(Path(pdf).name in title_of(h) for h in windows_of(pid)), 30)
    results["pdf_open_seconds"] = round(time.perf_counter() - started, 2)
    print("pdf opened in", results["pdf_open_seconds"], flush=True)
    time.sleep(1.5)
    memory = sum(p["WorkingSetSize"] for p in folder_processes(folder)) / 1e6
    results["memory_mb_with_pdf"] = round(memory)
    names = sorted({p["Name"] for p in folder_processes(folder)})
    check("PDF does not start the office engine", not any("soffice" in n for n in names), names)
    user32.PostMessageW(windows_of(pid)[0], 0x0010, 0, 0)
    process.wait(15)

    # PowerPoint: first open, then full screen.
    started = time.perf_counter()
    process = launch(exe, data, deck)
    pid = process.pid
    wait(lambda: any(Path(deck).name in title_of(h) for h in windows_of(pid)), 40)
    results["pptx_window_seconds"] = round(time.perf_counter() - started, 2)
    assert wait(lambda: any("soffice" in p["Name"] for p in folder_processes(folder)), 30)
    time.sleep(14)
    hwnd = next(h for h in windows_of(pid) if Path(deck).name in title_of(h))
    grab(out / "01-main-window.png")
    processes = folder_processes(folder)
    results["memory_mb_with_deck"] = {p["Name"]: round(p["WorkingSetSize"] / 1e6) for p in processes}
    check("no network connections", tcp_connections([p["ProcessId"] for p in processes]) == 0, [p["Name"] for p in processes])
    check("no PowerPoint started", not subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process POWERPNT -ErrorAction SilentlyContinue"], capture_output=True, text=True).stdout.strip())

    if not bring_forward(hwnd):
        check("James Viewer reached the foreground", False)
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return 1

    width, height = screen_size()
    press(VK_F11, pid)
    time.sleep(1.2)
    foreground = user32.GetForegroundWindow()
    full = rect_of(foreground)
    check("F11 window covers the whole monitor", full == (0, 0, width, height), f"{full} on {width}x{height}")
    check("F11 window has no title bar or border", (user32.GetWindowLongW(foreground, -16) & 0x00C40000) == 0)
    first = grab(out / "02-full-screen.png")
    # A 16:9 slide on a taller screen leaves black bands. Any toolbar or taskbar would show in them.
    bands = [first.crop((0, 0, width, 12)), first.crop((0, height - 12, width, height))] if height / width > 9 / 16 + 0.001 else []
    if bands:
        check("letterbox bands are pure black, so no taskbar or toolbar", all(band.getextrema() == ((0, 0), (0, 0), (0, 0)) for band in bands))
    arrow = ctypes.cast(user32.LoadCursorW(None, 32512), ctypes.c_void_p).value

    def pointer_hidden() -> bool:
        info = CursorInfo(cbSize=ctypes.sizeof(CursorInfo))
        user32.GetCursorInfo(ctypes.byref(info))
        return info.flags == 0 or info.hCursor != arrow

    started_waiting = time.perf_counter()
    hidden = wait(pointer_hidden, 3.0)  # a mouse that moved shows the pointer until it has rested
    check("cursor is hidden", hidden, f"after {time.perf_counter() - started_waiting:.2f}s more")
    time.sleep(0.5)
    check("cursor stays hidden while the mouse rests", pointer_hidden())

    press(VK_RIGHT, pid)
    time.sleep(0.5)
    second = grab(out / "03-next-slide.png")
    check("Right arrow changes the slide", difference(first, second) > 0.5, round(difference(first, second), 2))
    started = time.perf_counter()
    press(VK_RIGHT, pid)
    press(VK_LEFT, pid)
    time.sleep(0.5)
    press(VK_LEFT, pid)
    time.sleep(0.5)
    back = grab()
    check("Left arrow returns to the same slide", difference(first, back) < 0.05, round(difference(first, back), 3))
    press(VK_ESCAPE, pid)
    time.sleep(0.8)
    check("Esc returns to the main window", user32.GetForegroundWindow() == hwnd and rect_of(hwnd) != (0, 0, width, height))
    grab(out / "04-after-esc.png")

    # Real drag and drop through Windows.
    helper_python = str(Path(__file__).resolve().parents[2] / ".venv" / "Scripts" / "python.exe")
    if real_drag(pid, hwnd, drop, helper_python, data):
        opened = wait(lambda: any(Path(drop).name in title_of(h) for h in windows_of(pid)), 15)
        check("real drag and drop opens the file", opened)
        time.sleep(0.8)
        grab(out / "05-after-drop.png")
    else:
        check("real drag and drop opens the file", False, "helper did not start")

    user32.PostMessageW(hwnd, 0x0010, 0, 0)
    try:
        process.wait(20)
        check("closes cleanly", True)
    except subprocess.TimeoutExpired:
        check("closes cleanly", False)
        process.kill()
    time.sleep(1.5)
    check("no office process left behind", not folder_processes(folder), folder_processes(folder))

    if len(sys.argv) > 6:
        markdown_checks(exe, data, out, str(Path(sys.argv[6]).resolve()), folder)
    if len(sys.argv) > 7:
        long_pdf_checks(exe, data, out, str(Path(sys.argv[7]).resolve()))

    sessions =list((data / "sessions").glob("*")) if (data / "sessions").exists() else []
    check("temporary session files removed", not sessions, [p.name for p in sessions])
    (out / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    failed = [name for name, value in results.items() if isinstance(value, dict) and value.get("passed") is False]
    print("failed:", failed, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
