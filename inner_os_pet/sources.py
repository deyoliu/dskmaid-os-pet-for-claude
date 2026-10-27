"""Tail Claude Code and Codex session logs (read-only) and turn new lines into pet events.

Event kinds: "prompt" (user sent a message), "tool" (agent started/finished a tool),
"tool_error", "done" (turn finished), "aborted".
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

log = logging.getLogger(__name__)

ACTIVE_WINDOW_S = 30 * 60  # only poll files touched in the last 30 minutes
RESCAN_EVERY_S = 5.0


@dataclass
class Event:
    agent: str  # "claude" | "codex"
    session: str  # session id
    kind: str
    text: str = ""
    project: str = ""
    ts: float = field(default_factory=time.time)


_TAG_RE = re.compile(r"<[^>]+>")
_IMAGE_RE = re.compile(r"\[Image #\d+\]")


def _clean_prompt(text):
    # Slash commands arrive as <command-name>/foo</command-name><command-args>...</command-args>
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _project_name(cwd):
    return PureWindowsPath(cwd).name if cwd else ""


def parse_claude_line(line, session_hint=""):
    """Return a list of Events for one Claude Code transcript line."""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if d.get("isSidechain"):
        return []
    t = d.get("type")
    session = d.get("sessionId") or session_hint
    project = _project_name(d.get("cwd"))
    msg = d.get("message") or {}
    content = msg.get("content")
    ev = lambda kind, text="": Event("claude", session, kind, text, project)

    if t == "user":
        parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        if any(isinstance(p, dict) and p.get("type") == "tool_result" for p in parts):
            errs = [p for p in parts if isinstance(p, dict) and p.get("type") == "tool_result" and p.get("is_error")]
            return [ev("tool_error")] if errs else []
        text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
        if "[Request interrupted by user" in text:
            return [ev("aborted")]
        origin = d.get("origin")
        if origin is not None:
            is_human = isinstance(origin, dict) and origin.get("kind") == "human"
        else:  # older transcripts have no origin field
            is_human = not d.get("isMeta") and not text.lstrip().startswith("<")
        if not is_human or d.get("isMeta"):
            return []
        cleaned = _clean_prompt(_IMAGE_RE.sub(" ", text))
        has_image = any(isinstance(p, dict) and p.get("type") == "image" for p in parts)
        if has_image:
            cleaned = f"（附一張圖）{cleaned}" if cleaned else "（丟了一張圖過來）"
        return [ev("prompt", cleaned)] if cleaned else []

    if t == "assistant":
        tools = [p.get("name", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "tool_use"]
        return [ev("tool", tools[0])] if tools else []

    if t == "system" and d.get("subtype") in ("turn_duration", "stop_hook_summary"):
        return [ev("done")]
    return []


def parse_codex_line(line, session_hint=""):
    """Return a list of Events for one Codex rollout line."""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    p = d.get("payload") or {}
    t, pt = d.get("type"), p.get("type")
    session = p.get("thread_id") or p.get("session_id") or session_hint
    ev = lambda kind, text="": Event("codex", session, kind, text)

    if t == "session_meta":
        return [Event("codex", p.get("id") or session_hint, "meta", "", _project_name(p.get("cwd")))]
    if t != "event_msg":
        return []
    if pt == "task_complete":
        return [ev("done")]
    if pt == "turn_aborted":
        return [ev("aborted")]
    if pt == "item_completed":
        item = p.get("item") or {}
        it = item.get("type")
        if it == "UserMessage":
            text = " ".join(c.get("text", "") for c in item.get("content") or [] if isinstance(c, dict))
            cleaned = _clean_prompt(text)
            return [ev("prompt", cleaned)] if cleaned else []
        if it == "CommandExecution":
            code = item.get("exit_code")
            if code not in (None, 0):
                return [ev("tool_error")]
            return [ev("tool", "shell")]
        if it in ("FileChange", "Extension", "ImageView"):
            return [ev("tool", it)]
    return []


class _Tail:
    """Follows one JSONL file from a byte offset, tolerating partial trailing lines."""

    def __init__(self, path, offset):
        self.path = path
        self.offset = offset
        self.buf = b""

    def read_new_lines(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:  # truncated or replaced
            self.offset, self.buf = 0, b""
        if size == self.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read(size - self.offset)
        self.offset = size
        data = self.buf + chunk
        lines = data.split(b"\n")
        self.buf = lines.pop()  # incomplete last line (or b"")
        return [ln.decode("utf-8", "replace") for ln in lines if ln.strip()]


class SessionWatcher:
    """Polls Claude/Codex log folders and yields Events for lines written after startup."""

    def __init__(self, claude_dirs, codex_dirs):
        self.claude_dirs = claude_dirs
        self.codex_dirs = codex_dirs
        self.tails = {}  # path -> (_Tail, agent)
        self.codex_projects = {}  # session id -> project folder name
        self.last_scan = 0.0
        # Sizes of every log that exists at startup: those are followed from this offset, so
        # history is never replayed even if a long-idle session wakes up later.
        self.known_offsets = {}
        for path, _ in self._candidates():
            try:
                self.known_offsets[path] = path.stat().st_size
            except OSError:
                pass

    def _candidates(self):
        for d in self.claude_dirs:
            # top-level session files only; subagent transcripts live in nested folders
            for p in d.glob("*/*.jsonl"):
                yield p, "claude"
        for d in self.codex_dirs:
            for p in d.glob("**/rollout-*.jsonl"):
                yield p, "codex"

    def _rescan(self):
        now = time.time()
        for path, agent in self._candidates():
            if path in self.tails:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if now - mtime > ACTIVE_WINDOW_S:
                continue
            # Known files resume where we left them (or their startup size); files created
            # after startup are read from the beginning.
            self.tails[path] = (_Tail(path, self.known_offsets.get(path, 0)), agent)
            if agent == "codex":
                self._learn_codex_project(path)
            log.debug("following %s", path)
        # drop files that went quiet
        for path in [p for p, _ in self.tails.items() if self._stale(p, now)]:
            tail, _ = self.tails.pop(path)
            self.known_offsets[path] = tail.offset - len(tail.buf)

    def _stale(self, path, now):
        try:
            return now - path.stat().st_mtime > ACTIVE_WINDOW_S
        except OSError:
            return True

    def _learn_codex_project(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                first = f.readline()
        except OSError:
            return
        for e in parse_codex_line(first):
            if e.kind == "meta":
                self.codex_projects[e.session] = e.project

    def poll(self):
        if time.time() - self.last_scan > RESCAN_EVERY_S:
            self._rescan()
            self.last_scan = time.time()
        events = []
        for path, (tail, agent) in list(self.tails.items()):
            for line in tail.read_new_lines():
                if agent == "claude":
                    events.extend(parse_claude_line(line, path.stem))
                else:
                    for e in parse_codex_line(line, path.stem):
                        if e.kind == "meta":
                            self.codex_projects[e.session] = e.project
                            continue
                        e.project = e.project or self.codex_projects.get(e.session, "")
                        events.append(e)
        return events
