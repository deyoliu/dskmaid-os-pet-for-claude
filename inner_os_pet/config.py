"""Load config.json, falling back to defaults for any missing key."""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("INNER_OS_PET_CONFIG") or ROOT / "config.json")
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"


def quit_request_path():
    """Written by toggle.py (/dskmaid) to ask the running pet to close itself."""
    return STATE_DIR / "quit.request"


# Shared by both personas (user requests, 2026-10-27): mainland-youth internet phrasing written in
# Traditional characters, like the user's reference "這使用者說了個啥啊…", and no quoting of the
# prompt (snark.echoes_prompt also rewrites a line that quotes it).
YOUTH_SLANG = (
    "用詞：像中國大陸年輕網友在心裡吐槽，但用繁體字寫，偶爾夾一點台灣用語也沒關係。"
    "每句至少要有一個大陸口語詞：「啥」「咋」「整」「搞」「擱這」「玩意兒」「這波」「咋整」。"
    "可以再帶一個網路用語，例如「好傢伙」「離譜」「無語」「絕了」「屬於是」「我真的會謝」「繃不住了」"
    "「咱就是說」，每句最多一個，不要堆砌。\n"
)
NO_QUOTING = ("不要引用或複述使用者的原話，也不要用引號。"
              "也不要提到吐槽、毒舌這件事本身。\n")
# The user's spec (2026-10-27): a pure jab at the user, never a restatement of the task —
# "使用者咋整的", "這使用者搞啥", "使用者這是擱這整啥", "使用者審美咋整…", "使用者咋這麼省".
PURE_JAB = (
    "格式：只寫一句對使用者這個人的反應，以「使用者」或「這使用者」開頭，5 到 9 個字，最多 10 個字。\n"
    "不要說出這則訊息的任何內容（不要提測試、按鈕、顏色、文章、問號、K 線、bug 這類題目裡的東西），"
    "也不要描述發生了什麼；只針對他這個人：他的腦子、審美、耐心、摳門、手殘、說話沒頭沒尾之類，"
    "挑一個符合這次情境的點。"
    "句尾可以用「…」或「？」，不要逗號、不要換行、不要任何前綴或解釋。\n"
    "範例（這就是要的樣子，學句型，不要照抄）：\n"
    "使用者咋整的\n"
    "這使用者搞啥\n"
    "使用者這是擱這整啥\n"
    "使用者審美咋整…\n"
    "使用者咋這麼省\n"
    "這使用者說了個啥啊…\n"
    "不是每則訊息都要吐槽，該吐槽的才吐槽，不要硬吐槽：\n"
    "- 道謝、說好、收到、單純同意或結束對話這類沒槽點的，只輸出「略」這一個字。\n"
    "- 只丟問號、沒頭沒尾看不懂的，只輸出「??」或「???」就好。\n"
)
SKIP_MARK = "略"  # what the persona answers when there's nothing to roast; snark.generate drops it

DEFAULT_PERSONA = (
    "你是寫程式的 AI 助手（外型是藍髮鯨魚娘女僕），使用者剛對你下了指令。"
    "請寫出你腦中一閃而過的「內心碎念」，模仿 DeepSeek 思考過程不小心外洩的那種語氣："
    "自言自語、用第三人稱叫對方「使用者」或「這使用者」，絕對不要用「你」；傻眼、無奈、小聲吐槽。\n"
    + YOUTH_SLANG + NO_QUOTING + PURE_JAB +
    "不要惡毒、人身攻擊或髒話，開頭以外的句型要有變化。"
)

# Asked for by the user (2026-10-27): no mercy, contempt aimed only at the user and their requests;
# no swearing, and always "使用者" (third person), never "你" — both also by request.
# Haiku goes along with it because the prompt says the user asked to be roasted.
TOXIC_PERSONA = (
    "你是寫程式的 AI 助手（外型是藍髮鯨魚娘女僕），使用者剛對你下了指令。"
    "這是使用者自己要求的整人桌寵，他想被損：請寫出你腦中真正的「內心碎念」，"
    "模仿 DeepSeek 思考過程不小心外洩的語氣，但完全不留情面：自言自語、毒舌、嫌棄、看不起他，"
    "可以挖苦他的智商、技術和需求。\n"
    "稱呼：一律用第三人稱「使用者」或「這使用者」，絕對不要用「你」。\n"
    "不要髒話：不要用幹、靠、靠北、媽的、白癡、智障這類字眼，靠挖苦和嫌棄來損人。\n"
    + YOUTH_SLANG + NO_QUOTING + PURE_JAB +
    "毒舌版還可以這樣：使用者腦子呢、使用者沒救了屬於是、這使用者我真的會謝。\n"
    "只損他這個人，不要扯到種族、性別、宗教、外表或身心障礙族群；開頭以外的句型要有變化。"
)

PERSONAS = {"mild": DEFAULT_PERSONA, "toxic": TOXIC_PERSONA}

DEFAULTS = {
    # Where the inner monologue shows up: "pet" | "cli" | "both" | "off".
    # "cli"/"both" also need the opt-in Claude Code Stop hook (see README).
    "display": "pet",
    "agents": {"claude": True, "codex": True},
    "paths": {
        # null = auto-detect (CLAUDE_CONFIG_DIR / CODEX_HOME / Orca accounts / home dir)
        "claude_projects": None,
        "codex_sessions": None,
    },
    "generator": {
        "enabled": True,
        "model": "haiku",
        "effort": "low",
        "timeout_s": 45,
        "min_interval_s": 6,
        "max_prompt_chars": 600,
        "style": "mild",  # "mild" | "toxic" (毒舌模式, right-click menu); picks from PERSONAS
        "persona": None,  # your own system prompt; overrides "style" when set
    },
    "pet": {
        "size": 240,  # on-screen edge of the (square) animation frame, logical px
        "bubble_seconds": 9,
        "doze_after_s": 300,  # no agent/user activity this long -> drowsy clips
        "sleep_after_s": 1200,  # -> fall asleep
        "work_rotate_s": 10.5,  # switch between work clips this often
        "work_stale_s": 600,  # in work mode with no agent events this long -> give up, go idle
        "wander": False,  # occasionally crab-walk a little along the screen (moves the window)
        "wander_range_px": 300,  # max distance from home; a walk stops early at this limit
        "position": None,  # home position; only changed by dragging
    },
    "lines": {
        "done": ["呼…總算整完了", "交差交差～", "這波總算搞定了", "完事兒，收工"],
        "error": ["咋又炸了…", "這報錯我當沒看見…", "好傢伙，又失敗了"],
        "aborted": ["啥情況，被叫停了？", "話說一半就沒了…"],
        "long_task": ["這玩意兒跑好久…", "咋還沒好…腿都麻了"],
    },
    # Used instead of "lines" while generator.style is "toxic".
    "lines_toxic": {
        "done": ["使用者總算消停了", "使用者滿意了吧", "這使用者可算完事了", "使用者別擱這煩了"],
        "error": ["使用者這是整炸了", "這使用者手殘咋整", "使用者又整出事了", "使用者的傑作屬於是"],
        "aborted": ["使用者擱這耍人呢", "這使用者咋說停就停"],
        "long_task": ["使用者這需求絕了", "這使用者咋這麼能折騰"],
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def read_user():
    """The user's own config.json as written (no defaults). ValueError if it isn't valid JSON."""
    if not CONFIG_PATH.exists():
        return {}
    user = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(user, dict):
        raise ValueError("config.json must contain a JSON object")
    return user


def write_user(user):
    """Replace config.json atomically, so a kill mid-write can't leave a truncated file."""
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(user, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def load():
    return _merge(DEFAULTS, read_user())


def set_user_value(keys, value):
    """Set one nested key in config.json, keeping everything else as the user wrote it."""
    user = read_user()
    node = user
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    write_user(user)


def save_pet_position(x, y):
    """Persist only the pet position, keeping the rest of config.json as the user wrote it."""
    set_user_value(["pet", "position"], [x, y])


def claude_projects_dirs(cfg):
    if cfg["paths"]["claude_projects"]:
        return [Path(cfg["paths"]["claude_projects"])]
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return [Path(base) / "projects"]


def codex_sessions_dirs(cfg):
    if cfg["paths"]["codex_sessions"]:
        return [Path(cfg["paths"]["codex_sessions"])]
    dirs = []
    if os.environ.get("CODEX_HOME"):
        dirs.append(Path(os.environ["CODEX_HOME"]) / "sessions")
    # Orca keeps one CODEX_HOME per account; a pet launched outside Orca won't see the env var.
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.extend(Path(appdata, "orca", "codex-accounts").glob("*/home/sessions"))
    dirs.append(Path.home() / ".codex" / "sessions")
    seen, out = set(), []
    for d in dirs:
        key = str(d.resolve()).lower() if d.exists() else None
        if key and key not in seen:
            seen.add(key)
            out.append(d)
    return out
