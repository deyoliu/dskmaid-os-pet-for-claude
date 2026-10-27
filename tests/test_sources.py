"""Parser and tailer tests against fixture lines shaped like real Claude Code / Codex logs.

Run: python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inner_os_pet import config, snark  # noqa: E402
from inner_os_pet.sources import SessionWatcher, parse_claude_line, parse_codex_line  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def kinds(events):
    return [(e.kind, e.text) for e in events]


class ClaudeParser(unittest.TestCase):
    def parse_fixture(self):
        out = []
        for line in (FIX / "claude_session.jsonl").read_text(encoding="utf-8").splitlines():
            out.extend(parse_claude_line(line))
        return out

    def test_fixture_sequence(self):
        self.assertEqual(kinds(self.parse_fixture()), [
            ("prompt", "幫我修登入的 bug"),
            ("tool", "Bash"),
            ("tool_error", ""),
            ("tool", "Edit"),
            ("done", ""),
            ("prompt", "/review 這次的改動"),
            ("prompt", "（丟了一張圖過來）"),
            ("aborted", ""),
        ])

    def test_project_and_session(self):
        e = self.parse_fixture()[0]
        self.assertEqual((e.agent, e.session, e.project), ("claude", "s-claude-1", "my-app"))

    def test_ignores_meta_sidechain_and_notifications(self):
        # fixture contains a task-notification, an isMeta line and a sidechain prompt; none become prompts
        texts = [e.text for e in self.parse_fixture() if e.kind == "prompt"]
        self.assertNotIn("subagent prompt", " ".join(texts))
        self.assertFalse(any("task-notification" in t for t in texts))

    def test_bad_json(self):
        self.assertEqual(parse_claude_line("{not json"), [])


class CodexParser(unittest.TestCase):
    def test_fixture_sequence(self):
        out = []
        for line in (FIX / "codex_rollout.jsonl").read_text(encoding="utf-8").splitlines():
            out.extend(parse_codex_line(line))
        self.assertEqual(kinds(out), [
            ("meta", ""),
            ("prompt", "把 README 翻成英文"),
            ("tool", "shell"),
            ("tool_error", ""),
            ("tool", "FileChange"),
            ("done", ""),
        ])
        self.assertEqual(out[0].project, "rhysim")
        self.assertEqual(out[1].session, "t-codex-1")


class Watcher(unittest.TestCase):
    def test_only_new_lines_and_partial_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "projects" / "D--x-my-app"
            proj.mkdir(parents=True)
            f = proj / "s1.jsonl"
            old = json.dumps({"type": "user", "sessionId": "s1", "origin": {"kind": "human"},
                              "message": {"role": "user", "content": "舊訊息"}}, ensure_ascii=False)
            f.write_text(old + "\n", encoding="utf-8")
            w = SessionWatcher([Path(tmp) / "projects"], [])  # file predates startup -> follow from its end
            self.assertEqual(w.poll(), [])
            new = json.dumps({"type": "user", "sessionId": "s1", "cwd": "D:\\x\\my-app", "origin": {"kind": "human"},
                              "message": {"role": "user", "content": "新訊息"}}, ensure_ascii=False).encode("utf-8")
            with open(f, "ab") as fh:
                fh.write(new[:20])  # half a line: nothing yet
            self.assertEqual(w.poll(), [])
            with open(f, "ab") as fh:
                fh.write(new[20:] + b"\n")
            self.assertEqual(kinds(w.poll()), [("prompt", "新訊息")])

    def test_idle_session_waking_up_does_not_replay_history(self):
        from inner_os_pet import sources
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "projects" / "D--x"
            proj.mkdir(parents=True)
            f = proj / "s1.jsonl"
            row = lambda text: json.dumps({"type": "user", "sessionId": "s1", "origin": {"kind": "human"},
                                           "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n"
            f.write_text(row("歷史1") + row("歷史2"), encoding="utf-8")
            w = SessionWatcher([Path(tmp) / "projects"], [])
            w.poll()
            self.assertIn(f, w.tails)
            orig = sources.ACTIVE_WINDOW_S
            try:
                sources.ACTIVE_WINDOW_S = -1  # everything counts as stale -> dropped
                w.last_scan = 0
                w.poll()
                self.assertNotIn(f, w.tails)
            finally:
                sources.ACTIVE_WINDOW_S = orig
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(row("回來了"))
            w.last_scan = 0
            self.assertEqual(kinds(w.poll()), [("prompt", "回來了")])

    def test_new_file_after_startup_read_from_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "projects" / "D--x"
            proj.mkdir(parents=True)
            w = SessionWatcher([Path(tmp) / "projects"], [])
            (proj / "new.jsonl").write_text(json.dumps({"type": "user", "sessionId": "n", "origin": {"kind": "human"},
                                                        "message": {"role": "user", "content": "第一句"}}) + "\n",
                                            encoding="utf-8")
            w.last_scan = 0
            self.assertEqual(kinds(w.poll()), [("prompt", "第一句")])

    def test_nested_subagent_files_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "projects" / "D--x" / "s1" / "subagents"
            sub.mkdir(parents=True)
            (sub / "agent-1.jsonl").write_text("", encoding="utf-8")
            w = SessionWatcher([Path(tmp) / "projects"], [])
            w.poll()
            self.assertEqual(w.tails, {})


class Snark(unittest.TestCase):
    def test_clean_line(self):
        self.assertEqual(snark.clean_line("「內心OS：又改需求了是吧…」\n多餘的第二行"), "又改需求了是吧…")
        self.assertEqual(snark.clean_line(""), "")

    def test_only_the_roast_clause_is_kept(self):
        # The user wants the roast without Haiku's set-up clause in front of it.
        cases = {
            "昨天能跑今天壞…使用者咋整的": "使用者咋整的",
            "就丟個問號，使用者這是擱這整啥": "使用者這是擱這整啥",
            "顏色來回橫跳，使用者審美咋整…": "使用者審美咋整…",
            "使用者想學DeepSeek？離譜": "使用者想學DeepSeek？",  # "離譜" is just a tag
            "綠的剛搞完就嫌醜 離譜": "綠的剛搞完就嫌醜",  # a space between CJK splits too
            "使用者扔個問號 想讓我猜咋整…": "想讓我猜咋整…",
            "純屬想一口吃成胖子。": "純屬想一口吃成胖子",
            "bug 不就使用者自己整的": "使用者自己整的",  # Latin + space isn't a pause; from_user trims
            "這使用者說了個啥啊…": "這使用者說了個啥啊…",
            "使用者是要把測試全刪？": "使用者是要把測試全刪？",
        }
        for line, want in cases.items():
            self.assertEqual(snark.clean_line(line), want)

    def test_the_jab_starts_at_the_user(self):
        # The user's spec: a pure jab at 使用者, no restating the task in front of it.
        cases = {
            "測試跑完了沒使用者咋不看？": "使用者咋不看？",
            "改來改去這使用者審美咋整…": "這使用者審美咋整…",
            "這使用者搞啥": "這使用者搞啥",
            "使用者咋這麼省": "使用者咋這麼省",
            "題目都懶得給這使用者": "題目都懶得給這使用者",  # nothing after 使用者: left alone
            "一個問號就想讓我猜啥…": "一個問號就想讓我猜啥…",  # no 使用者 at all: left alone
        }
        for line, want in cases.items():
            self.assertEqual(snark.clean_line(line), want)

    def test_nothing_to_roast_gives_no_line(self):
        # The user: don't force a jab on "好 謝謝"; the persona answers 略 and the bubble just goes.
        for answer in ("略", "「略」", "略。", "（略）"):
            self.assertTrue(snark.is_skip(snark.clean_line(answer)), answer)
        for answer in ("??", "？？？", "使用者略懂", ""):
            self.assertFalse(snark.is_skip(snark.clean_line(answer)), answer)
        orig = snark._call
        snark._call = lambda gen_cfg, exe, msg, timeout: "略"
        try:
            line, _ = snark.generate(dict(config.DEFAULTS["generator"]), "好 謝謝", "claude", "p", exe="claude.exe")
        finally:
            snark._call = orig
        self.assertIsNone(line)
        self.assertEqual(snark.clean_line("??"), "??", "a bare ?? reaction survives cleaning")

    def test_long_line_is_capped(self):
        out = snark.clean_line("一" * 80)
        self.assertEqual(len(out), snark.LINE_MAX)
        self.assertTrue(out.endswith("…"))

    def test_recent_lines_go_into_the_message(self):
        msg = snark.build_user_message("改一下", "claude", "p", 600, recent=("好吧，又來了…",))
        self.assertIn("好吧，又來了…", msg)
        self.assertNotIn("最近", snark.build_user_message("改一下", "claude", "p", 600))

    def test_persona_comes_from_style_unless_overridden(self):
        self.assertEqual(snark.persona_for({"style": "toxic", "persona": None}), config.TOXIC_PERSONA)
        self.assertEqual(snark.persona_for({"style": "mild", "persona": None}), config.DEFAULT_PERSONA)
        self.assertEqual(snark.persona_for({"style": "nonsense"}), config.DEFAULT_PERSONA)
        self.assertEqual(snark.persona_for({"style": "toxic", "persona": "自訂"}), "自訂")
        cmd = snark.build_command(dict(config.DEFAULTS["generator"], style="toxic"), "claude.exe")
        self.assertEqual(cmd[cmd.index("--system-prompt") + 1], config.TOXIC_PERSONA)

    def test_toxic_canned_lines_say_user_and_do_not_swear(self):
        # The user asked: no swearing, and "使用者" instead of "你".
        for kind, lines in config.DEFAULTS["lines_toxic"].items():
            for line in lines:
                self.assertNotIn("你", line, line)
                self.assertTrue(line.startswith(("使用者", "這使用者")), line)
                for word in ("幹", "靠", "媽的", "白癡", "智障"):
                    self.assertNotIn(word, line, line)
        examples = config.PURE_JAB.split("範例")[1].split("不是每則訊息")[0]
        self.assertNotIn("你", examples)
        for example in examples.split("\n")[1:]:
            if example:
                self.assertTrue(example.startswith(("使用者", "這使用者")), example)
                self.assertEqual(snark.clean_line(example), example, "an example must survive cleaning as is")

    def test_echo_detection(self):
        prompt = "按鈕顏色改回原本的藍色，上次那個綠色不好看"
        self.assertTrue(snark.echoes_prompt("看到綠色不好看還要改", prompt))  # 5 chars copied
        self.assertTrue(snark.echoes_prompt("這使用者就一句「好 謝謝」", "好 謝謝"))  # quote marks
        self.assertFalse(snark.echoes_prompt("顏色來回橫跳，使用者審美咋整…", prompt))
        self.assertFalse(snark.echoes_prompt("使用者想學DeepSeek？離譜", "讓她吐槽更像DeepSeek那種碎念"),
                         "a name repeated isn't quoting")

    def test_a_quoting_line_is_rewritten_once(self):
        calls = []

        def fake_call(gen_cfg, exe, msg, timeout):
            calls.append(msg)
            return "看到綠色不好看還要改" if len(calls) == 1 else "顏色來回橫跳，咋整"

        orig = snark._call
        snark._call = fake_call
        try:
            cfg = dict(config.DEFAULTS["generator"])
            line, _ = snark.generate(cfg, "上次那個綠色不好看", "claude", "p", exe="claude.exe")
        finally:
            snark._call = orig
        self.assertEqual(line, "顏色來回橫跳，咋整")
        self.assertEqual(len(calls), 2)
        self.assertIn("不要引用", calls[1])

    def test_default_persona_is_one_valid_arg(self):
        p = config.DEFAULT_PERSONA
        self.assertIn("DeepSeek", p)
        self.assertNotIn("\x00", p)
        self.assertIn("\n", p)  # sections are separated; subprocess passes it as one quoted argument

    def test_command_is_isolated(self):
        cfg = {"model": "haiku", "persona": "p", "effort": "low"}
        cmd = snark.build_command(cfg, "claude.exe")
        for flag in ("--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")

    def test_env_scrubbed(self):
        import os
        os.environ["ORCA_PANE_KEY"] = "x"
        try:
            self.assertNotIn("ORCA_PANE_KEY", snark._env())
        finally:
            del os.environ["ORCA_PANE_KEY"]


class CliHook(unittest.TestCase):
    def setUp(self):
        from inner_os_pet import app, config
        import cli_hook
        self.app, self.config, self.hook = app, config, cli_hook
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = config.STATE_DIR
        config.STATE_DIR = Path(self.tmp.name)

    def tearDown(self):
        self.config.STATE_DIR = self.orig
        self.tmp.cleanup()

    def test_pending_then_ready(self):
        self.assertEqual(self.hook.lookup("s", 0), (None, False))  # unknown session: don't wait
        self.app.write_latest("s", "claude")  # generation started
        self.assertEqual(self.hook.lookup("s", 0), (None, True))
        self.app.write_latest("s", "claude", "又改需求了是吧…", 4.2)
        entry, pending = self.hook.lookup("s", 0)
        self.assertEqual((entry["text"], pending), ("又改需求了是吧…", False))
        # already printed: nothing to show and nothing pending
        self.assertEqual(self.hook.lookup("s", entry["ts"]), (None, False))


if __name__ == "__main__":
    unittest.main()
