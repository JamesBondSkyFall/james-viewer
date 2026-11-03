"""Start the office repeatedly with its console output captured, and show what a crashing run says.

  office_crash_log.py <office dir> <starts> <document> <output folder>
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from james_viewer.core import office_engine, paths


def main() -> None:
    office, starts, document, out = Path(sys.argv[1]).resolve(), int(sys.argv[2]), str(Path(sys.argv[3]).resolve()), Path(sys.argv[4]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    program = office / "program"
    seed = office / office_engine.PROFILE_SEED
    env = office_engine._child_env()
    env["SAL_LOG"] = "+WARN+INFO.vcl.app+INFO.desktop.app"
    failures = 0
    messages: dict[str, int] = {}
    for number in range(starts):
        pipe = f"james_log_{uuid.uuid4().hex[:8]}"
        profile = out / f"profile-{number}"
        if seed.is_dir():
            shutil.copytree(seed, profile)
        else:
            office_engine._write_profile(profile)
        url = "file:///" + str(profile).replace("\\", "/")
        log_path = out / f"log-{number}.txt"
        with open(log_path, "wb") as log:
            soffice = subprocess.Popen(
                [str(program / "soffice.com"), f"-env:UserInstallation={url}", "--headless", "--invisible", "--norestore", "--nologo", "--nodefault", "--nolockcheck", "--nofirststartwizard", f"--accept=pipe,name={pipe};urp;"],
                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            bridge = subprocess.Popen(
                [str(program / "python.exe"), str(paths.worker_script()), pipe],
                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8", bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            job = office_engine._Job()
            job.add(soffice)
            job.add(bridge)
            ready = json.loads(bridge.stdout.readline() or "{}")
            bridge.stdin.write(json.dumps({"op": "pdf", "input": document, "output": str(out / f"out-{number}.pdf")}) + "\n")
            bridge.stdin.flush()
            reply = json.loads(bridge.stdout.readline() or "{}")
            time.sleep(0.3)
            code = soffice.poll()
            job.terminate()
            job.wait_empty(5)
            job.close()
        text = log_path.read_text(encoding="utf-8", errors="replace")
        failed = not reply.get("ok")
        if failed:
            failures += 1
            tail = "\n".join(text.splitlines()[-14:])
            print(f"--- start {number} failed: ready={ready} exit={code} error={reply.get('error', '')[:80]}\n{tail}\n", flush=True)
        else:
            try:
                log_path.unlink(missing_ok=True)
            except OSError:
                pass  # the console launcher can hold it for a moment after being ended
        shutil.rmtree(profile, ignore_errors=True)
    print(f"{failures} failures in {starts} starts", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("JAMES_VIEWER_DATA_DIR", str(ROOT / ".build" / "data-crashlog"))
    main()
