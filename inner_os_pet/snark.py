"""Generate the one-line inner monologue with a throwaway `claude -p` call.

The call is isolated so it never shows up as a session of its own:
--no-session-persistence (no transcript for the watcher to pick up), --setting-sources ""
(no user hooks, so Orca doesn't see a phantom session), no tools, no MCP, and the
Orca/Claude Code env vars are stripped.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_SCRUB_PREFIXES = ("ORCA_", "CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_JOB_DIR")
CREATE_NO_WINDOW = 0x08000000


def _env():
    return {k: v for k, v in os.environ.items() if not k.startswith(_SCRUB_PREFIXES)}


def find_claude():
    """Prefer the real claude.exe over npm's .CMD shim so arguments skip cmd.exe parsing."""
    found = shutil.which("claude")
    if not found:
        return None
    p = Path(found)
    if p.suffix.lower() in (".cmd", ".bat", ""):
        exe = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return found


def persona_for(gen_cfg):
    """A custom `persona` wins; otherwise the preset named by `style` (unknown -> mild)."""
    return gen_cfg.get("persona") or config.PERSONAS.get(gen_cfg.get("style"), config.DEFAULT_PERSONA)


def build_command(gen_cfg, exe):
    cmd = [
        exe, "-p",
        "--model", gen_cfg["model"],
        "--no-session-persistence",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--tools", "",
        "--output-format", "json",
        "--system-prompt", persona_for(gen_cfg),
    ]
    if gen_cfg.get("effort"):
        cmd += ["--effort", gen_cfg["effort"]]
    return cmd


LINE_MAX = 14  # the user wants a short jab; the persona asks for 5–9 chars, this is the hard cap
CLAUSE_MIN = 4  # a last clause shorter than this is a tag ("離譜", "絕了"): keep the one before
_CLAUSE = re.compile(r"[^，,…？?！!。；;、]+[，,…？?！!。；;、]*")
_CLAUSE_TEXT = re.compile(r"[，,…？?！!。；;、\s]")
_CJK_SPACE = re.compile(r"(?<=[㐀-鿿])\s+(?=[㐀-鿿])")  # "嫌醜 離譜": a pause too


def last_clause(line):
    """'昨天能跑今天壞…使用者咋整的' -> '使用者咋整的': the user wants the roast without the
    set-up clause Haiku tends to put in front of it."""
    line = _CJK_SPACE.sub("，", line)
    parts = [m.group().strip() for m in _CLAUSE.finditer(line) if m.group().strip()]
    keep = parts[-1] if parts else line
    if len(parts) >= 2 and len(_CLAUSE_TEXT.sub("", keep)) < CLAUSE_MIN:
        keep = parts[-2]
    return keep.lstrip("，,、；; ").rstrip("，,、；;。") or line


def from_user(line):
    """'測試跑完了沒使用者咋不看？' -> '使用者咋不看？': the jab starts at 使用者, never with the task."""
    idx = line.rfind("使用者")
    if idx <= 0 or (idx == 1 and line[0] == "這"):
        return line
    start = idx - 1 if line[idx - 1] == "這" else idx
    rest = _CLAUSE_TEXT.sub("", line[idx + 3:])
    return line[start:] if len(rest) >= 2 else line  # "…使用者" alone says nothing


def build_user_message(prompt, agent, project, max_chars, recent=()):
    prompt = prompt if len(prompt) <= max_chars else prompt[:max_chars] + "…"
    who = "Claude Code" if agent == "claude" else "Codex"
    where = f"（專案：{project}）" if project else ""
    msg = f"你是 {who}{where}。使用者剛傳來：\n{prompt}"
    if recent:  # each call is stateless; without this Haiku keeps reusing the same opener and ending
        msg += "\n\n你最近幾次的碎念（這次換個開頭和收尾，別重複這些句型）：\n" + "\n".join(f"- {r}" for r in recent)
    return msg


def clean_line(text):
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    line = line.strip("「」\"'“” ")
    for prefix in ("內心OS：", "內心 OS：", "OS：", "內心OS:", "OS:", "內心碎念：", "內心碎念:"):
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
    line = from_user(last_clause(line.strip("「」\"'“” ")))
    if len(line) <= LINE_MAX:
        return line
    # Too long: end at the last pause that fits instead of mid-word.
    cut = max(line.rfind(p, 0, LINE_MAX) for p in "…？！，。、")
    if cut >= LINE_MAX // 2:
        return line[:cut + 1] if line[cut] in "…？！" else line[:cut] + "…"
    return line[:LINE_MAX - 1] + "…"


def _kill_tree(proc):
    """Kill the CLI and anything it spawned. subprocess.run's own timeout handling waits, with no
    limit, for every process that inherited the output pipes, which could hang this thread."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    proc.kill()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        log.warning("generator output pipes still open after kill; abandoning them")


ECHO_RUN = 5  # this many characters copied from the prompt in a row counts as quoting it
RETRY_MIN_S = 10  # a quoting line is rewritten only if this much of the timeout is left
NO_QUOTE_NOTE = "\n\n（上一版引用了使用者的原話。這次完全不要引用或複述原話，也不要用引號，用自己的話吐槽。）"


def _norm(text):
    # Only CJK: repeating a name like "DeepSeek" or "K線" isn't quoting the user.
    return "".join(re.findall(r"[㐀-鿿]", text))


def echoes_prompt(line, prompt):
    """True if the line quotes the user's message (the user asked for no quoting)."""
    if any(q in line for q in "「」『』“”\""):
        return True
    l, p = _norm(line), _norm(prompt)
    return any(l[i:i + ECHO_RUN] in p for i in range(len(l) - ECHO_RUN + 1))


def is_skip(line):
    """The persona answers 略 when the user's message has nothing worth a jab."""
    return bool(line) and re.sub(r"[\W_]+", "", line) == config.SKIP_MARK


def _call(gen_cfg, exe, msg, timeout):
    """One `claude -p` run -> cleaned line or None."""
    # Run outside the project folder so its CLAUDE.md/AGENTS.md aren't picked up as memory.
    workdir = config.STATE_DIR
    workdir.mkdir(exist_ok=True)
    try:
        proc = subprocess.Popen(
            build_command(gen_cfg, exe), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=_env(), cwd=str(workdir),
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as e:
        log.warning("generator failed to start: %s", e)
        return None
    try:
        stdout, stderr = proc.communicate(msg.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("generator timed out after %.0fs", timeout)
        _kill_tree(proc)
        return None
    out = stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        log.warning("generator exit %s: %s %s", proc.returncode, out[:200], stderr.decode("utf-8", "replace")[:200])
        return None
    try:
        result = json.loads(out).get("result", "")
    except ValueError:
        result = out
    return clean_line(result) or None


def generate(gen_cfg, prompt, agent, project, exe=None, recent=()):
    """Return (line or None, seconds). Blocking; run it off the UI thread.

    `recent` = the last few lines shown, so the next one varies its phrasing. A line that quotes
    the prompt is rewritten once, within the same overall timeout."""
    start = time.time()
    exe = exe or find_claude()
    if not exe:
        log.warning("claude executable not found on PATH")
        return None, 0.0
    msg = build_user_message(prompt, agent, project, gen_cfg["max_prompt_chars"], recent)
    line = _call(gen_cfg, exe, msg, gen_cfg["timeout_s"])
    if is_skip(line):  # nothing worth roasting ("好 謝謝"): no line at all, the bubble just goes
        log.info("nothing to roast in this prompt")
        return None, time.time() - start
    left = gen_cfg["timeout_s"] - (time.time() - start)
    if line and echoes_prompt(line, prompt) and left >= RETRY_MIN_S:
        log.info("line quoted the prompt, rewriting: %r", line)
        line = _call(gen_cfg, exe, msg + NO_QUOTE_NOTE, left) or line
    return line, time.time() - start
