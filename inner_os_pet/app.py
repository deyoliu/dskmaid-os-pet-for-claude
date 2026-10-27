"""Wire the session watcher, the inner-monologue generator and the pet window together."""
import collections
import faulthandler
import json
import logging
import os
import random
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from . import config, snark
from .pet import PetStartupError, PetWindow
from .sources import SessionWatcher

log = logging.getLogger("inner_os_pet")
LONG_TASK_S = 120
LATEST_KEEP = 50  # sessions remembered for the CLI hook
RECENT_KEEP = 3  # last lines passed to the generator to avoid repeating openers/endings
GEN_GRACE_S = 30  # a generation running this much longer than its timeout is given up on
FAULT_LOG_MAX = 1_000_000


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    h = RotatingFileHandler(config.LOG_DIR / "pet.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)
    # A failed rotation (pet.log held open by a viewer) must not raise into the caller.
    logging.raiseExceptions = False


def badge(agent, project):
    name = "Claude" if agent == "claude" else "Codex"
    return f"{name} · {project}" if project else name


def write_latest(session, agent, text=None, elapsed=0.0):
    """Record the newest line per session so the optional CLI Stop hook can print it.

    Called with text=None when a generation starts, so the hook knows a line is on its way.
    """
    config.STATE_DIR.mkdir(exist_ok=True)
    latest = config.STATE_DIR / "latest_os.json"
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    entry = data.get(session) or {"agent": agent, "text": "", "elapsed": 0, "ts": 0}
    if text is None:
        entry["requested"] = time.time()
    else:
        entry.update(agent=agent, text=text, elapsed=round(elapsed, 1), ts=time.time())
    data[session] = entry
    if len(data) > LATEST_KEEP:
        data = dict(sorted(data.items(), key=lambda kv: max(kv[1]["ts"], kv[1].get("requested", 0)))[-LATEST_KEEP:])
    tmp = latest.with_suffix(".tmp")
    for _ in range(5):
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, latest)
            return
        except OSError:  # the CLI hook may be reading it; Windows won't replace an open file
            time.sleep(0.05)
    log.warning("could not update %s", latest)


class Controller(QObject):
    line_ready = Signal(str, str, str, str, float)  # request id, session, agent, line, seconds

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = config.load()
        self.pet = PetWindow(self.cfg)
        self.pet.menu_action.connect(self.on_menu)
        self.line_ready.connect(self.on_line)
        self.watcher = self._make_watcher()
        self.focus = None  # (agent, session, project) the pet is currently following
        self.turn_started = {}  # session -> time of last prompt
        self.long_warned = set()
        self.last_line = None
        self.recent_lines = collections.deque(maxlen=RECENT_KEEP)  # fed back so phrasing varies
        self.pending = None  # request id of the in-flight generation shown in the bubble
        self.last_gen_at = 0.0
        self.queued = None
        self.gen_busy = False
        self.gen_started_at = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(400)
        self.pet.show()
        log.info("started; claude=%s codex=%s", self.watcher.claude_dirs, self.watcher.codex_dirs)

    def _make_watcher(self):
        a = self.cfg["agents"]
        return SessionWatcher(
            config.claude_projects_dirs(self.cfg) if a["claude"] else [],
            config.codex_sessions_dirs(self.cfg) if a["codex"] else [],
        )

    # ---------- events ----------
    def poll(self):
        if self._quit_requested():
            return
        try:
            events = self.watcher.poll()
        except Exception:
            log.exception("watcher poll failed")
            return
        for e in events:
            try:
                self.handle(e)
            except Exception:  # one bad event must not drop the rest of the batch
                log.exception("handling a %s event failed", e.kind)
        try:
            self._check_generator()
            self._check_long_task()
            self._maybe_start_queued()
        except Exception:
            log.exception("poll housekeeping failed")

    def _quit_requested(self):
        path = config.quit_request_path()
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            pass  # toggle.py may still hold it open; quitting matters more than the file
        log.info("quit requested by toggle.py")
        self.on_menu("quit")
        return True

    def _check_generator(self):
        """Give up on a generation that never came back, so later prompts still get lines."""
        limit = float(self.cfg["generator"]["timeout_s"]) + GEN_GRACE_S
        if self.gen_busy and time.time() - self.gen_started_at > limit:
            log.warning("generator gave no answer after %.0f s; giving up on it", limit)
            self.gen_busy = False
            self.pending = None

    def handle(self, e):
        log.info("event %s %s %s %s", e.agent, e.session[:8], e.kind, e.text[:60])
        b = badge(e.agent, e.project)
        if e.kind == "prompt":
            self.focus = (e.agent, e.session, e.project)
            self.turn_started[e.session] = time.time()
            self.long_warned.discard(e.session)
            self.pet.agent_event("prompt")
            self.request_line(e.session, e.agent, e.project, e.text)
            return
        # The pet follows whichever session the user prompted last; other sessions' tool/done
        # events are ignored so two busy agents don't make it flicker.
        if self.focus and self.focus[1] != e.session:
            return
        if self.focus is None:
            self.focus = (e.agent, e.session, e.project)
        if e.kind == "tool":
            # a turn already running when the pet started has no prompt event; count it from here
            self.turn_started.setdefault(e.session, time.time())
            self.pet.agent_event("tool")
        elif e.kind == "tool_error":
            self.pet.agent_event("error")
            self.canned("error", b, chance=0.5)
        elif e.kind == "done":
            # Claude logs two end-of-turn markers; only the first one counts
            if self.turn_started.pop(e.session, None) is not None:
                self.pet.agent_event("done")
                if not self.pending:
                    self.canned("done", b, chance=0.35)
        elif e.kind == "aborted":
            self.turn_started.pop(e.session, None)
            self.pet.agent_event("aborted")
            self.canned("aborted", b)

    def _check_long_task(self):
        if not self.focus:
            return
        s = self.focus[1]
        started = self.turn_started.get(s)
        if started and time.time() - started > LONG_TASK_S and s not in self.long_warned and not self.pending:
            self.long_warned.add(s)
            self.pet.agent_event("long")
            self.canned("long_task", badge(self.focus[0], self.focus[2]))

    def canned(self, kind, b, chance=1.0):
        if self.cfg["display"] in ("pet", "both") and random.random() < chance:
            pool = "lines_toxic" if self.cfg["generator"].get("style") == "toxic" else "lines"
            lines = self.cfg[pool].get(kind) or []
            if lines:
                self.pet.show_line(random.choice(lines), b)

    # ---------- generator ----------
    def request_line(self, session, agent, project, text):
        if not self.cfg["generator"]["enabled"] or self.cfg["display"] == "off":
            return
        rid = f"{session}:{time.time()}"
        self.pending = rid
        if self.cfg["display"] in ("cli", "both"):
            write_latest(session, agent)
        if self.cfg["display"] in ("pet", "both"):
            self.pet.show_thinking(badge(agent, project))
        # only the newest prompt matters; older queued ones are dropped
        self.queued = (rid, session, agent, project, text)
        self._maybe_start_queued()

    def _maybe_start_queued(self):
        if not self.queued or self.gen_busy:
            return
        if time.time() - self.last_gen_at < self.cfg["generator"]["min_interval_s"]:
            return
        rid, session, agent, project, text = self.queued
        self.queued = None
        self.gen_busy = True
        self.gen_started_at = self.last_gen_at = time.time()
        gen_cfg = dict(self.cfg["generator"])
        recent = tuple(self.recent_lines)

        def work():
            try:
                line, secs = snark.generate(gen_cfg, text, agent, project, recent=recent)
            except Exception:
                log.exception("generator crashed")
                line, secs = None, 0.0
            self.line_ready.emit(rid, session, agent, line or "", secs)

        threading.Thread(target=work, daemon=True).start()

    def on_line(self, rid, session, agent, line, secs):
        self.gen_busy = False
        log.info("line %.1fs %r", secs, line)
        if not line:
            if self.pending == rid:
                self.pending = None
                self.pet.hide_bubble()
            return
        write_latest(session, agent, line, secs)
        self.recent_lines.append(line)
        project = self.focus[2] if self.focus and self.focus[1] == session else ""
        self.last_line = (line, badge(agent, project), f"已思考（用時 {max(1, round(secs))} 秒）")
        if self.pending == rid:
            self.pending = None
        if self.cfg["display"] in ("pet", "both"):
            self.pet.show_line(*self.last_line)
        else:  # display switched to cli/off while this line was being written
            self.pet.hide_bubble()

    # ---------- menu ----------
    def on_menu(self, action):
        if action == "quit":
            self.pet.shutdown()
            self.app.quit()
        elif action == "repeat":
            if self.last_line:
                self.pet.show_line(*self.last_line)
        elif action == "toggle_generator":
            self._set_user_value(["generator", "enabled"], not self.cfg["generator"]["enabled"])
        elif action == "toggle_toxic":
            toxic = self.cfg["generator"].get("style") == "toxic"
            self._set_user_value(["generator", "style"], "mild" if toxic else "toxic")
            self.recent_lines.clear()  # don't feed one style's lines to the other
        elif action == "toggle_wander":
            self._set_user_value(["pet", "wander"], not self.cfg["pet"]["wander"])
        elif action.startswith("display:"):
            self._set_user_value(["display"], action.split(":", 1)[1])
            if self.cfg["display"] in ("cli", "off"):
                self.pet.hide_bubble()
        elif action == "open_config":
            if not config.CONFIG_PATH.exists():
                config.write_user({})
            os.startfile(config.CONFIG_PATH)
        elif action == "open_log":
            os.startfile(config.LOG_DIR)
        elif action == "reload":
            try:
                self.cfg = config.load()
            except ValueError as e:
                self._config_broken(e)
                return
            self.pet.set_cfg(self.cfg)
            self.watcher = self._make_watcher()

    def _set_user_value(self, keys, value):
        try:
            config.set_user_value(keys, value)
            self.cfg = config.load()
        except ValueError as e:  # never overwrite a config.json the user is halfway through editing
            self._config_broken(e)
            return
        self.pet.set_cfg(self.cfg)

    def _config_broken(self, err):
        log.warning("config.json is not valid JSON: %s", err)
        self.pet.show_line("config.json 格式有誤，先沿用原本的設定", "設定檔")


class _LogStream:
    """Stands in for stdout/stderr under pythonw (where they are None) so stray writes are logged."""

    _busy = threading.local()

    def __init__(self, level):
        self.level = level

    def write(self, text):
        # Logging reports its own failures on stderr; without this guard that recurses forever.
        if getattr(self._busy, "on", False):
            return
        text = text.rstrip()
        if text:
            self._busy.on = True
            try:
                logging.getLogger("stdio").log(self.level, text)
            finally:
                self._busy.on = False

    def flush(self):
        pass


def _install_crash_logging():
    if sys.stdout is None:
        sys.stdout = _LogStream(logging.INFO)
    if sys.stderr is None:
        sys.stderr = _LogStream(logging.ERROR)
    sys.excepthook = lambda *exc: log.critical("uncaught exception", exc_info=exc)
    threading.excepthook = lambda a: log.critical(
        "uncaught exception in thread %s", a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def _enable_fault_log():
    """Native crashes (PyAV, Qt) leave no Python traceback; faulthandler writes one to a file."""
    path = config.LOG_DIR / "fault.log"
    try:
        if path.exists() and path.stat().st_size > FAULT_LOG_MAX:
            path.unlink()
        f = open(path, "a", encoding="utf-8")  # kept open for the process lifetime
    except OSError:
        return
    faulthandler.enable(file=f)
    globals()["_fault_file"] = f


def main():
    config.STATE_DIR.mkdir(exist_ok=True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(config.STATE_DIR / "pet.lock"))
    if not lock.tryLock(100):
        # pythonw has no console; say so instead of silently doing nothing
        QMessageBox.information(None, "內心 OS 桌寵", "桌寵已經在執行了（在螢幕上找找她吧）。")
        return 1
    setup_logging()  # only the instance that owns the lock touches pet.log (rotation needs it closed)
    try:
        config.quit_request_path().unlink(missing_ok=True)  # left over from a pet that died before reading it
    except OSError:
        pass
    _install_crash_logging()
    _enable_fault_log()
    try:
        ctl = Controller(app)
    except PetStartupError as e:
        # pythonw has no console: tell the user in a dialog, keep the details in the log
        log.error("startup failed: %s", e)
        QMessageBox.critical(None, "內心 OS 桌寵", f"{e}\n\n詳細紀錄：{config.LOG_DIR / 'pet.log'}")
        return 1
    except ValueError as e:  # config.json isn't valid JSON
        log.error("startup failed: config.json: %s", e)
        QMessageBox.critical(None, "內心 OS 桌寵",
                             f"設定檔 config.json 格式有誤，請修正或刪除它：\n{e}\n\n{config.CONFIG_PATH}")
        return 1
    except Exception as e:
        log.exception("startup failed")
        QMessageBox.critical(None, "內心 OS 桌寵", f"啟動失敗：{e!r}\n\n詳細紀錄：{config.LOG_DIR / 'pet.log'}")
        return 1
    app.aboutToQuit.connect(ctl.pet.shutdown)
    return app.exec()
