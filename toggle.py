"""Show the pet if it isn't running, close it if it is. Prints one line for the caller.

Used by the Claude Code `/dskmaid` command (claude-plugin/). Also runnable by hand:
    python toggle.py            toggle
    python toggle.py on|off     only start / only close
    python toggle.py status     just report
Exit code 0 when the pet ended up in the requested state, 1 otherwise.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QLockFile  # noqa: E402

from inner_os_pet import config, snark  # noqa: E402  (light imports: no window, no PyAV)

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000  # survive the job object the calling CLI may run us in
START_WAIT_S = 20
QUIT_WAIT_S = 6


def _lock():
    return QLockFile(str(config.STATE_DIR / "pet.lock"))


def running_pid():
    """PID of the running pet, or None. A stale lock (dead process) counts as not running."""
    lock = _lock()
    if lock.tryLock(0):
        lock.unlock()
        return None
    info = lock.getLockInfo()  # PySide6: (pid, hostname, appname), or a falsy value if unreadable
    return info[0] if info and info[0] else -1


def _log_text_from(offset):
    path = config.LOG_DIR / "pet.log"
    try:
        with open(path, "rb") as f:
            if f.seek(0, 2) < offset:  # rotated meanwhile
                offset = 0
            f.seek(offset)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def start():
    log_path = config.LOG_DIR / "pet.log"
    offset = log_path.stat().st_size if log_path.exists() else 0
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    kwargs = dict(cwd=str(ROOT), env=snark._env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen([exe, str(ROOT / "run.pyw")], creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                                **kwargs)
    except OSError:  # the job forbids breakaway; the pet then lives as long as the job does
        proc = subprocess.Popen([exe, str(ROOT / "run.pyw")], creationflags=flags, **kwargs)
    deadline = time.time() + START_WAIT_S
    while time.time() < deadline:
        text = _log_text_from(offset)
        if "startup failed" in text:
            return False, f"桌寵啟動失敗，請看跳出的視窗或 {log_path}"
        if " started;" in text:
            return True, "桌寵出來了（右鍵可以打開選單，再輸入一次 /dskmaid 收起來）"
        if proc.poll() is not None:
            return False, f"桌寵啟動失敗（結束碼 {proc.returncode}），請看 {log_path}"
        time.sleep(0.2)
    return True, "桌寵還在啟動中，稍等一下應該就會出現"


def stop(pid):
    request = config.quit_request_path()
    request.write_text(str(time.time()), encoding="utf-8")
    deadline = time.time() + QUIT_WAIT_S
    while time.time() < deadline:
        if running_pid() is None:
            return True, "桌寵收起來了"
        time.sleep(0.2)
    # Not answering (hung, or a dialog is open): end it the hard way.
    if pid and pid > 0:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        time.sleep(0.5)
    try:
        request.unlink(missing_ok=True)
    except OSError:
        pass
    if running_pid() is None:
        return True, "桌寵沒有回應，已強制關閉"
    return False, f"關不掉桌寵（PID {pid}），請到工作管理員結束 pythonw.exe"


def main(argv):
    mode = argv[0] if argv else "toggle"
    if mode not in ("toggle", "on", "off", "status"):
        print("用法：/dskmaid [on|off|status]（不加參數就是切換）")
        return 1
    config.STATE_DIR.mkdir(exist_ok=True)
    pid = running_pid()
    if mode == "status":
        print(f"桌寵正在執行（PID {pid}）" if pid else "桌寵沒有在執行")
        return 0
    if pid and mode in ("toggle", "off"):
        ok, msg = stop(pid)
    elif not pid and mode in ("toggle", "on"):
        ok, msg = start()
    else:
        ok, msg = True, "桌寵本來就在了" if pid else "桌寵本來就沒開"
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main(sys.argv[1:]))
