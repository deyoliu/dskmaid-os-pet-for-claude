"""Controller / startup robustness tests (no real sessions, no Haiku calls, no window shown).

Run: python -m unittest discover -s tests -p "test_app.py"
"""
import io
import atexit
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = Path(tempfile.mkdtemp(prefix="inner-os-app-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)  # fake sessions, temp config
os.environ["INNER_OS_PET_CONFIG"] = str(_TMP / "config.json")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from inner_os_pet import app as pet_app, config, snark  # noqa: E402
from inner_os_pet.sources import Event  # noqa: E402

QAPP = QApplication.instance() or QApplication([])


class FakePet:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name,) + a)


def make_controller(**cfg_over):
    """A Controller without its __init__ (no window, no watcher, no timer)."""
    ctl = pet_app.Controller.__new__(pet_app.Controller)
    QObject.__init__(ctl)
    ctl.app = None
    ctl.cfg = config._merge(config.DEFAULTS, cfg_over)
    ctl.pet = FakePet()
    ctl.focus = None
    ctl.turn_started = {}
    ctl.long_warned = set()
    ctl.last_line = None
    ctl.recent_lines = pet_app.collections.deque(maxlen=pet_app.RECENT_KEEP)
    ctl.pending = None
    ctl.last_gen_at = 0.0
    ctl.queued = None
    ctl.gen_busy = False
    ctl.gen_started_at = 0.0
    return ctl


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(dir=_TMP))
        self._orig_state = config.STATE_DIR
        config.STATE_DIR = self.state

    def tearDown(self):
        config.STATE_DIR = self._orig_state

    def test_line_after_display_switched_to_cli_hides_the_thinking_bubble(self):
        ctl = make_controller(display="cli")
        ctl.pending = "r1"
        ctl.on_line("r1", "s1", "claude", "又改需求了是吧…", 4.2)
        self.assertIn(("hide_bubble",), ctl.pet.calls)
        self.assertNotIn("show_line", [c[0] for c in ctl.pet.calls])
        self.assertIsNone(ctl.pending)

    def test_recent_lines_are_fed_back_to_the_generator(self):
        ctl = make_controller()
        for i in range(5):
            ctl.on_line(f"r{i}", "s1", "claude", f"碎念{i}", 1.0)
        self.assertEqual(list(ctl.recent_lines), ["碎念2", "碎念3", "碎念4"])
        seen = {}
        orig = pet_app.snark.generate
        done = threading.Event()

        def fake(gen_cfg, text, agent, project, exe=None, recent=()):
            seen["recent"] = recent
            done.set()
            return "x", 0.1

        pet_app.snark.generate = fake
        try:
            ctl.last_gen_at = 0
            ctl.queued = ("r9", "s1", "claude", "", "改一下")
            ctl._maybe_start_queued()
            self.assertTrue(done.wait(5))
        finally:
            pet_app.snark.generate = orig
        self.assertEqual(seen["recent"], ("碎念2", "碎念3", "碎念4"))

    def test_stuck_generation_is_given_up(self):
        ctl = make_controller()
        ctl.gen_busy, ctl.pending = True, "r1"
        ctl.gen_started_at = time.time() - ctl.cfg["generator"]["timeout_s"] - pet_app.GEN_GRACE_S - 1
        ctl._check_generator()
        self.assertFalse(ctl.gen_busy)
        self.assertIsNone(ctl.pending)

    def test_one_bad_event_does_not_drop_the_rest(self):
        ctl = make_controller()
        seen = []

        def handle(e):
            seen.append(e.kind)
            if e.kind == "tool":
                raise RuntimeError("boom")

        ctl.handle = handle

        class W:
            def poll(self):
                return [Event("claude", "s", "tool"), Event("claude", "s", "done")]

        ctl.watcher = W()
        with self.assertLogs("inner_os_pet", level="ERROR"):
            ctl.poll()
        self.assertEqual(seen, ["tool", "done"])

    def test_write_latest_survives_a_locked_file(self):
        latest = self.state / "latest_os.json"
        latest.write_text("{}", encoding="utf-8")
        real_replace = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, "Access is denied")
            real_replace(src, dst)

        pet_app.os.replace = flaky
        try:
            pet_app.write_latest("s1", "claude", "內心 OS", 1.0)
        finally:
            pet_app.os.replace = real_replace
        self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["s1"]["text"], "內心 OS")

    def test_broken_config_is_never_overwritten(self):
        bad = '{"pet": {"size": 300,}}'  # trailing comma: the user is mid-edit
        config.CONFIG_PATH.write_text(bad, encoding="utf-8")
        try:
            ctl = make_controller()
            ctl._set_user_value(["pet", "wander"], True)
            self.assertEqual(config.CONFIG_PATH.read_text(encoding="utf-8"), bad)
            self.assertEqual(ctl.pet.calls[-1][0], "show_line")
            with self.assertRaises(ValueError):
                config.load()
        finally:
            config.CONFIG_PATH.unlink()

    def test_toxic_mode_toggles_and_switches_the_canned_lines(self):
        try:
            ctl = make_controller()
            ctl.recent_lines.append("溫和的一句")
            ctl.on_menu("toggle_toxic")
            self.assertEqual(config.read_user(), {"generator": {"style": "toxic"}})
            self.assertEqual(ctl.cfg["generator"]["style"], "toxic")
            self.assertEqual(list(ctl.recent_lines), [])
            ctl.canned("done", "Claude")
            self.assertIn(ctl.pet.calls[-1][1], config.DEFAULTS["lines_toxic"]["done"])
            ctl.on_menu("toggle_toxic")
            self.assertEqual(ctl.cfg["generator"]["style"], "mild")
            ctl.canned("done", "Claude")
            self.assertIn(ctl.pet.calls[-1][1], config.DEFAULTS["lines"]["done"])
        finally:
            config.CONFIG_PATH.unlink(missing_ok=True)

    def test_config_writes_are_atomic_and_keep_user_keys(self):
        config.write_user({"display": "both", "pet": {"size": 200}})
        try:
            config.save_pet_position(10, 20)
            user = config.read_user()
            self.assertEqual(user, {"display": "both", "pet": {"size": 200, "position": [10, 20]}})
            self.assertFalse(config.CONFIG_PATH.with_name(config.CONFIG_PATH.name + ".tmp").exists())
        finally:
            config.CONFIG_PATH.unlink()


class QuitRequestTests(unittest.TestCase):
    """/dskmaid (toggle.py) closes the pet by dropping state/quit.request."""

    def setUp(self):
        self.state = Path(tempfile.mkdtemp(dir=_TMP))
        self._orig_state = config.STATE_DIR
        config.STATE_DIR = self.state

    def tearDown(self):
        config.STATE_DIR = self._orig_state

    def test_poll_consumes_the_request_and_quits(self):
        ctl = make_controller()
        quits = []
        ctl.app = type("App", (), {"quit": lambda self: quits.append(1)})()
        polled = []
        ctl.watcher = type("W", (), {"poll": lambda self: polled.append(1) or []})()
        config.quit_request_path().write_text("1", encoding="utf-8")
        ctl.poll()
        self.assertEqual(quits, [1])
        self.assertEqual(polled, [], "a quitting pet must not handle more events")
        self.assertIn(("shutdown",), ctl.pet.calls)
        self.assertFalse(config.quit_request_path().exists())

    def test_no_request_no_quit(self):
        ctl = make_controller()
        quits = []
        ctl.app = type("App", (), {"quit": lambda self: quits.append(1)})()
        ctl.watcher = type("W", (), {"poll": lambda self: []})()
        ctl.poll()
        self.assertEqual(quits, [])
        self.assertNotIn(("shutdown",), ctl.pet.calls)


class ToggleTests(unittest.TestCase):
    def setUp(self):
        import toggle
        self.toggle = toggle
        self.state = Path(tempfile.mkdtemp(dir=_TMP))
        self._orig_state = config.STATE_DIR
        config.STATE_DIR = self.state

    def tearDown(self):
        config.STATE_DIR = self._orig_state

    def _lock_holder(self):
        """A process that holds pet.lock and never reads quit.request (a hung pet)."""
        code = ("import sys, time; from PySide6.QtCore import QLockFile; "
                f"l = QLockFile({str(self.state / 'pet.lock')!r}); assert l.tryLock(1000); "
                "print('locked', flush=True); time.sleep(60)")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        return proc

    def test_not_running_and_stale_lock(self):
        self.assertIsNone(self.toggle.running_pid())
        proc = self._lock_holder()
        self.assertEqual(self.toggle.running_pid(), proc.pid)
        proc.kill()
        proc.wait()
        self.assertIsNone(self.toggle.running_pid(), "a dead holder's lock must count as not running")

    def test_a_pet_that_ignores_the_request_is_killed(self):
        proc = self._lock_holder()
        orig = self.toggle.QUIT_WAIT_S
        self.toggle.QUIT_WAIT_S = 1
        try:
            ok, msg = self.toggle.stop(proc.pid)
        finally:
            self.toggle.QUIT_WAIT_S = orig
        self.assertTrue(ok, msg)
        self.assertIn("強制", msg)
        self.assertIsNotNone(proc.wait(5))
        self.assertFalse(config.quit_request_path().exists(), "a leftover request would close the next pet")

    def test_bad_argument_prints_usage(self):
        out = io.StringIO()
        saved = sys.stdout
        sys.stdout = out
        try:
            self.assertEqual(self.toggle.main(["bogus"]), 1)
        finally:
            sys.stdout = saved
        self.assertIn("用法", out.getvalue())


class LoggingTests(unittest.TestCase):
    def test_log_stream_does_not_recurse_when_a_handler_fails(self):
        class Failing(logging.Handler):  # like RotatingFileHandler when pet.log is held open
            def emit(self, record):
                try:
                    raise OSError("rotation failed: file in use")
                except OSError:
                    self.handleError(record)  # reports on sys.stderr, i.e. back into logging

        root = logging.getLogger()
        h = Failing()
        root.addHandler(h)
        saved_err, saved_flag = sys.stderr, logging.raiseExceptions
        sys.stderr = pet_app._LogStream(logging.ERROR)
        logging.raiseExceptions = True  # worst case: handleError writes a report to stderr
        try:
            logging.getLogger("inner_os_pet").warning("this must not recurse")
        finally:
            sys.stderr, logging.raiseExceptions = saved_err, saved_flag
            root.removeHandler(h)


class GeneratorKillTests(unittest.TestCase):
    def test_timeout_kills_the_whole_process_tree(self):
        # A CLI whose child inherits the output pipes: without killing the tree, waiting for the
        # pipes to close would block until the child exits (60 s here).
        code = ("import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")
        proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        with self.assertRaises(subprocess.TimeoutExpired):
            proc.communicate(b"", timeout=1)
        t = time.perf_counter()
        with self.assertNoLogs("inner_os_pet.snark", level="WARNING") if hasattr(self, "assertNoLogs") else io.StringIO():
            snark._kill_tree(proc)
        self.assertLess(time.perf_counter() - t, 5, "killing the generator blocked")
        self.assertIsNotNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
