"""Controls the bundled LibreOffice used to render and convert PowerPoint files.

Nothing here starts until the first Office document is opened, so PDF-only use
never pays for it.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from . import paths
from .errors import OfficeEngineError
from .pptx_engine import SlideSpec, build_deck

FIRST_CHUNK = 4
CHUNK_SIZE = 32
PROFILE_SEED = "james-profile"
ENGINE_ATTEMPTS = 3
CALL_TIMEOUT = 600.0
START_TIMEOUT = 45.0

_REGISTRY = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Setup/Office"><prop oor:name="ooSetupInstCompleted" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Setup/Office/Factories/org.openoffice.Setup:Factory['com.sun.star.presentation.PresentationDocument']"><prop oor:name="ooSetupFactoryDefaultFilter" oor:op="fuse"><value>Impress MS PowerPoint 2007 XML</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="UseOpenCL" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowTipOfTheDay" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Save/Document"><prop oor:name="CreateBackup" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Save/Document"><prop oor:name="AutoSave" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Recovery/AutoSave"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Recovery/RecoveryInfo"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Linguistic/SpellChecking"><prop oor:name="IsSpellAuto" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Filter/Microsoft/Import"><prop oor:name="ImportWWFieldsToODF" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Font/Substitution"><prop oor:name="Replacement" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Font/Substitution"><prop oor:name="FontPairs" oor:op="fuse"><value>
{pairs}
</value></prop></item>
</oor:items>
"""

# Chinese font names that an English Windows lists only under their Latin names, and
# Chinese fonts Windows does not ship, mapped to the closest font it does have.
_FONT_PAIRS = {
    "微软雅黑": "Microsoft YaHei",
    "宋体": "SimSun",
    "新宋体": "NSimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "楷体_GB2312": "KaiTi",
    "仿宋": "FangSong",
    "仿宋_GB2312": "FangSong",
    "等线": "DengXian",
    "等线 Light": "DengXian Light",
    "华文细黑": "Microsoft YaHei",
    "华文黑体": "Microsoft YaHei",
    "华文宋体": "SimSun",
    "华文楷体": "KaiTi",
    "华文仿宋": "FangSong",
    "华文中宋": "SimSun",
    "方正姚体": "Microsoft YaHei",
    "方正舒体": "KaiTi",
    "隶书": "KaiTi",
    "幼圆": "Microsoft YaHei",
    "思源黑体": "Microsoft YaHei",
    "思源宋体": "SimSun",
    "苹方": "Microsoft YaHei",
    "PingFang SC": "Microsoft YaHei",
    "阿里巴巴普惠体": "Microsoft YaHei",
    "阿里巴巴普惠体 B": "Microsoft YaHei",
    "Alibaba PuHuiTi": "Microsoft YaHei",
    "Alibaba PuHuiTi B": "Microsoft YaHei",
    "Alibaba PuHuiTi M": "Microsoft YaHei",
    "Alibaba PuHuiTi R": "Microsoft YaHei",
    "Alibaba PuHuiTi L": "Microsoft YaHei Light",
    "Alibaba PuHuiTi H": "Microsoft YaHei",
    "Source Han Sans CN": "Microsoft YaHei",
    "Source Han Serif CN": "SimSun",
    "Noto Sans CJK SC": "Microsoft YaHei",
    "Noto Serif CJK SC": "SimSun",
    "Heiti SC": "Microsoft YaHei",
    "Songti SC": "SimSun",
}


def _write_profile(profile: Path) -> None:
    user = profile / "user"
    user.mkdir(parents=True, exist_ok=True)
    registry = user / "registrymodifications.xcu"
    if registry.exists():
        return
    pairs = "\n".join(
        f"<it>{source};{target};true;false</it>" for source, target in _FONT_PAIRS.items()
    )
    registry.write_text(_REGISTRY.replace("<value>\n{pairs}\n</value>", f"<value>{pairs}</value>"), encoding="utf-8")


class _Job:
    """A Windows job object, so no office process can outlive James Viewer."""

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes",
        )]

    class _ExtendedLimit(ctypes.Structure):
        pass

    _ExtendedLimit._fields_ = [
        ("Basic", _BasicLimit),
        ("Io", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self):
        kernel = ctypes.windll.kernel32
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.handle = kernel.CreateJobObjectW(None, None)
        limits = self._ExtendedLimit()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    def add(self, process: subprocess.Popen) -> None:
        ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle)))

    def active(self) -> int:
        info = self._Accounting()
        kernel = ctypes.windll.kernel32
        kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]
        kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None)
        return info.ActiveProcesses

    def wait_empty(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.active() == 0:
                return True
            time.sleep(0.02)
        return self.active() == 0

    def terminate(self) -> None:
        kernel = ctypes.windll.kernel32
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        kernel = ctypes.windll.kernel32
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(self.handle)


class OfficeStartTimeout(OfficeEngineError):
    """The office neither came up nor exited."""


def _child_env() -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "QT_", "PYSIDE", "SHIBOKEN", "TCL_", "TK_"))
    }
    env["SAL_DISABLE_OPENCL"] = "1"
    return env


def create_profile_seed(office: Path, sample: Path) -> Path:
    """Build time: let the office finish one normal first run and keep the profile it saved."""
    office = office.resolve()
    target = office / PROFILE_SEED
    shutil.rmtree(target, ignore_errors=True)
    for attempt in range(6):
        worker = OfficeWorker(office, 0)
        worker.start()
        profile = worker.profile
        registry = profile / "user" / "registrymodifications.xcu"
        written_size = registry.stat().st_size
        output = profile.parent / f"seed-{uuid.uuid4().hex[:8]}.pdf"
        reply = worker.call("pdf", input=str(sample.resolve()), output=str(output))
        output.unlink(missing_ok=True)
        if not reply.get("ok"):
            worker.stop()
            continue
        worker.bridge.stdin.write(json.dumps({"op": "quit"}) + "\n")
        worker.bridge.stdin.flush()
        worker.job.wait_empty(20.0)
        # The office adds its own settings when it saves on exit, so the file must have grown.
        saved = registry.exists() and registry.stat().st_size > written_size and "</oor:items>" in registry.read_text(encoding="utf-8")
        if saved:
            for leftover in (".lock", "user/.lock", ".~lock"):
                (profile / leftover).unlink(missing_ok=True)
            for folder in ("user/temp", "user/backup", "crash", "user/crash"):
                shutil.rmtree(profile / folder, ignore_errors=True)
            shutil.copytree(profile, target)
        worker.stop()
        if saved:
            return target
    raise OfficeEngineError("The office engine did not complete a first run while building.")


def _purge_old_profiles() -> None:
    """Remove profiles left behind by runs that ended abruptly."""
    root = paths.data_dir() / "office"
    if not root.exists():
        return
    for entry in root.iterdir():
        owner = entry.name.split("-", 1)[0]
        if not owner.isdigit() or not paths._process_alive(int(owner)):
            shutil.rmtree(entry, ignore_errors=True)


class OfficeWorker:
    def __init__(self, office: Path, slot: int):
        self.office = office.resolve()
        self.slot = slot
        self.pipe = f"james_viewer_{os.getpid()}_{slot}_{uuid.uuid4().hex[:6]}"
        self.closing = False
        self.soffice: subprocess.Popen | None = None
        self.bridge: subprocess.Popen | None = None
        self.job: _Job | None = None
        self.profile: Path | None = None
        self.last_used = time.monotonic()

    def _spawn(self, args: list[str], **kwargs) -> subprocess.Popen:
        if self.job is None:
            self.job = _Job()
        ctypes.windll.kernel32.SetDllDirectoryW(None)  # do not hand the packaged DLL folder to children
        process = subprocess.Popen(args, env=_child_env(), creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)
        self.job.add(process)
        return process

    def start(self) -> None:
        program = self.office / "program"
        # Every start gets its own copy of a profile that has already completed a first run. Measured
        # over 80 starts: a profile's first run crashes on its first document about 30% of the time
        # and takes twice as long, while a finished profile almost never does.
        # A relative profile URL makes LibreOffice show a blocking "bootstrap.ini is corrupt" dialog.
        _purge_old_profiles()
        profile = (paths.data_dir() / "office" / f"{os.getpid()}-{uuid.uuid4().hex[:8]}").resolve()
        self.profile = profile
        seed = self.office / PROFILE_SEED
        if seed.is_dir():
            shutil.copytree(seed, profile)
        else:
            _write_profile(profile)
        # This has to be a real URL. The default data folder is "James Viewer", and an unencoded space
        # stops the office from ever coming up.
        profile_url = profile.as_uri()
        self.soffice = self._spawn(
            [
                str(program / "soffice.exe"),
                f"-env:UserInstallation={profile_url}",
                "--headless",
                "--invisible",
                "--norestore",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                f"--accept=pipe,name={self.pipe};urp;",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.bridge = self._spawn(
            [str(program / "python.exe"), str(paths.worker_script()), self.pipe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            bufsize=1,
        )
        reply = self._read_line(START_TIMEOUT, starting=True)
        if not reply.get("ready"):
            self.stop()
            raise OfficeEngineError(f"The office engine failed to start: {reply.get('error', 'no response')}")

    def alive(self) -> bool:
        return self.bridge is not None and self.bridge.poll() is None

    def _read_line(self, timeout: float, *, starting: bool = False) -> dict:
        result: queue.Queue = queue.Queue()
        stream = self.bridge.stdout

        def reader():
            try:
                result.put(stream.readline())
            except (OSError, ValueError) as exc:
                result.put(exc)

        threading.Thread(target=reader, daemon=True).start()
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = result.get(timeout=0.2)
                break
            except queue.Empty:
                pass
            if self.closing:
                self.stop()
                raise OfficeEngineError("James Viewer is closing.")
            if starting and self.soffice.poll() is not None:
                self.stop()
                raise OfficeEngineError("The office engine stopped while it was starting.")
            if time.monotonic() > deadline:
                self.stop()
                if starting:
                    raise OfficeStartTimeout("The office engine did not start. PowerPoint files cannot be displayed right now.")
                raise OfficeEngineError("The office engine stopped responding and was restarted.")
        if isinstance(line, Exception) or not line:
            self.stop()
            raise OfficeEngineError("The office engine stopped unexpectedly and was restarted.")
        return json.loads(line)

    def call(self, op: str, timeout: float = CALL_TIMEOUT, **args) -> dict:
        if not self.alive():
            self.start()
        self.last_used = time.monotonic()
        self.bridge.stdin.write(json.dumps({"op": op, **args}) + "\n")
        self.bridge.stdin.flush()
        reply = self._read_line(timeout)
        self.last_used = time.monotonic()
        return reply

    def kill(self) -> None:
        """End a call in progress, from another thread. The thread making the call cleans up."""
        self.closing = True
        job = self.job
        if job is not None:
            job.terminate()

    def stop(self) -> None:
        """Stop the office and return only once every process of it has gone.

        A new office started on the same profile while the old one is still exiting attaches to
        the old one and dies with it. Its own shutdown also crashes often enough to matter, and a
        crash there can damage the profile. Nothing it holds needs saving, so it is simply ended.
        """
        if self.job is not None:
            self.job.terminate()
            self.job.wait_empty(5.0)
            self.job.close()
            self.job = None
        if self.profile is not None:
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = None
        for process in (self.bridge, self.soffice):
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
        self.bridge = None
        self.soffice = None


def describe_error(error: str, name: str) -> str:
    return f"The office engine could not process {name}. Details: {error}"


def _engine_died(error: str) -> bool:
    return "DisposedException" in error or "bridge" in error.lower()


class OfficeEngine:
    """One office worker, shared in turn. Measured on real decks, more workers did not help."""

    def __init__(self, office: Path | None = None):
        self.office = office or paths.office_dir()
        self._lock = threading.RLock()
        self._closed = False
        self._worker: OfficeWorker | None = None

    @property
    def available(self) -> bool:
        return self.office is not None

    def _require_office(self) -> None:
        if self.office is None:
            raise OfficeEngineError(
                "The office engine is missing from this copy of James Viewer, so PowerPoint files cannot "
                "be displayed. PDF files still work. Reinstall James Viewer to restore it."
            )

    def _run(self, op: str, name: str, **args) -> dict:
        self._require_office()
        with self._lock:
            if self._closed:
                raise OfficeEngineError("James Viewer is closing.")
            if self._worker is None:
                self._worker = OfficeWorker(self.office, 0)
            reply = self._call_with_restarts(op, args)
        if not reply.get("ok"):
            raise OfficeEngineError(describe_error(reply.get("error", "unknown error"), name))
        return reply

    def _call_with_restarts(self, op: str, args: dict) -> dict:
        """If the office process crashes, as opposed to rejecting the file, start a new one and retry."""
        for attempt in range(ENGINE_ATTEMPTS):
            if self._closed:
                raise OfficeEngineError("James Viewer is closing.")
            try:
                reply = self._worker.call(op, **args)
            except OfficeStartTimeout:
                raise  # a start that hangs would only hang again, with the user still waiting
            except OfficeEngineError as exc:
                if attempt == ENGINE_ATTEMPTS - 1:
                    raise
                reply = {"ok": False, "error": f"DisposedException after: {exc}"}
            if reply.get("ok") or not _engine_died(reply.get("error", "")):
                return reply
            self._worker.stop()
        return reply

    @staticmethod
    def _job(source: Path, output: Path) -> dict:
        return {"input": str(Path(source).resolve()), "output": str(Path(output).resolve())}

    def to_pdf(self, source: Path, output: Path) -> dict:
        reply = self._run("pdf", source.name, **self._job(source, output))
        if not output.exists():
            raise OfficeEngineError(f"The office engine produced no pages for {source.name}.")
        return reply

    def word_to_pdf(self, source: Path, output: Path) -> None:
        self._run("wordpdf", source.name, **self._job(source, output))
        if not output.exists():
            raise OfficeEngineError(f"The office engine produced no pages for {source.name}.")

    def word_to_docx(self, source: Path, output: Path) -> None:
        self._run("docx", source.name, **self._job(source, output))
        if not output.exists():
            raise OfficeEngineError(f"The office engine could not convert {source.name} to DOCX.")

    def to_pptx(self, source: Path, output: Path) -> None:
        self._run("pptx", source.name, **self._job(source, output))
        if not output.exists():
            raise OfficeEngineError(f"The office engine could not convert {source.name} to PPTX.")

    def render_specs(self, base: str, specs: list[SlideSpec], work_dir: Path, *, first_number: int = 1, label: str = "chunk") -> Path:
        """Render slides to one PDF with a page per spec, using a stripped temporary deck."""
        deck = work_dir / f"{label}.pptx"
        pdf = work_dir / f"{label}.pdf"
        build_deck(base, specs, deck, first_slide_number=first_number, render_only=True)
        try:
            reply = self.to_pdf(deck, pdf)
        finally:
            deck.unlink(missing_ok=True)
        if reply.get("pages") != len(specs):
            raise OfficeEngineError(
                f"The office engine returned {reply.get('pages')} pages for {len(specs)} slides."
            )
        return pdf

    def render_deck(
        self,
        path: Path,
        slide_count: int,
        out_dir: Path,
        on_chunk: Callable[[list[int], Path], None],
    ) -> None:
        """Render a PPTX in chunks, calling back as each finishes. The opening slides come first."""
        base = str(path)
        chunks: list[list[int]] = []
        first = list(range(min(FIRST_CHUNK, slide_count)))
        if first:
            chunks.append(first)
        for start in range(len(first), slide_count, CHUNK_SIZE):
            chunks.append(list(range(start, min(start + CHUNK_SIZE, slide_count))))

        def render(number: int, indexes: list[int]) -> tuple[list[int], Path]:
            specs = [SlideSpec(base, index) for index in indexes]
            pdf = self.render_specs(base, specs, out_dir, first_number=indexes[0] + 1, label=f"chunk-{number:04d}")
            return indexes, pdf

        for number, chunk in enumerate(chunks):
            indexes, pdf = render(number, chunk)
            on_chunk(indexes, pdf)

    def stop_if_idle(self, idle_seconds: float = 300.0) -> None:
        """Give the office process's memory back once it has not been needed for a while."""
        if not self._lock.acquire(blocking=False):
            return
        try:
            worker = self._worker
            if worker is not None and worker.alive() and time.monotonic() - worker.last_used > idle_seconds:
                worker.stop()
        finally:
            self._lock.release()

    def close(self) -> None:
        self._closed = True
        worker = self._worker
        if worker is not None:
            worker.kill()  # a call in progress would otherwise hold the lock, and freeze the window, for minutes
        with self._lock:
            if self._worker is not None:
                self._worker.stop()


def reset_profiles() -> None:
    shutil.rmtree(paths.data_dir() / "office", ignore_errors=True)
