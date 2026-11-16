# -*- coding: utf-8 -*-
"""История диалога (п.19): ответ строится по ПОСЛЕДНЕЙ реплике пользователя,
история используется только для разрешения отсылок («а какая там неустойка?»)."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from llm_client import ApiError, LlmClient
from runtime_utils import LRU
from textutil import normalize_text, tokenize, truncate

ROLE_WORDS = {
    "пользователь": "user", "юзер": "user", "user": "user", "клиент": "user",
    "сотрудник": "user", "вопрос": "user", "human": "user", "q": "user",
    "ассистент": "assistant", "assistant": "assistant", "бот": "assistant", "bot": "assistant",
    "ответ": "assistant", "юрист": "assistant", "ai": "assistant", "a": "assistant",
}
_ROLE_RE = re.compile(
    r"(?:(?<=^)|(?<=\s))(Пользователь|Юзер|User|Клиент|Сотрудник|Вопрос|Human|"
    r"Ассистент|Assistant|Бот|Bot|Ответ|Юрист|AI)\s*:\s*", re.I)
_ANAPHORA = {"он", "она", "оно", "они", "его", "ее", "их", "этот", "эта", "это", "эти", "этого",
             "этой", "этом", "тот", "там", "тут", "такой", "такая", "такие", "данн", "указанн",
             "вышеуказанн", "тогда", "нему", "ней", "ним", "них", "аналогичн", "также", "тоже",
             "еще", "ещё", "иначе"}
_ANAPHORA_STEMS = {"данн", "указанн", "вышеуказанн", "аналогичн"}
_FOLLOW_START = re.compile(r"^\s*(а|и|ну|тогда|а если|а что|а как|а какой|а какая|а сколько|"
                           r"а когда|а кто|и если|значит)\b", re.I)


@dataclass
class Dialog:
    question: str                         # последняя реплика пользователя
    history: list[tuple[str, str]] = field(default_factory=list)  # (role, text) до неё
    raw: str = ""

    @property
    def prev_user(self) -> list[str]:
        return [t for r, t in self.history if r == "user"]


def _from_json(raw: str) -> Optional[Dialog]:
    s = raw.strip()
    if not s or s[0] not in "[{":
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    msgs = obj.get("messages") or obj.get("history") if isinstance(obj, dict) else obj
    if isinstance(obj, dict) and not msgs:
        q = obj.get("question") or obj.get("query") or obj.get("text")
        return Dialog(str(q), [], raw) if q else None
    if not isinstance(msgs, list):
        return None
    turns: list[tuple[str, str]] = []
    for m in msgs:
        if isinstance(m, dict):
            role = ROLE_WORDS.get(str(m.get("role", "user")).lower(), "user")
            text = m.get("content") or m.get("text") or ""
            if isinstance(text, list):
                text = "".join(str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in text)
            turns.append((role, str(text).strip()))
        elif isinstance(m, str):
            turns.append(("user", m.strip()))
    return _finish(turns, raw)


def _finish(turns: list[tuple[str, str]], raw: str) -> Optional[Dialog]:
    turns = [(r, t) for r, t in turns if t]
    if not turns:
        return None
    last_user = max((i for i, (r, _) in enumerate(turns) if r == "user"), default=None)
    if last_user is None:
        return Dialog(turns[-1][1], turns[:-1], raw)
    return Dialog(turns[last_user][1], turns[:last_user], raw)


def parse_dialog(cell: str) -> Dialog:
    raw = (cell or "").strip()
    d = _from_json(raw)
    if d:
        return d
    matches = list(_ROLE_RE.finditer(raw))
    if not matches:
        return Dialog(raw, [], raw)
    turns: list[tuple[str, str]] = []
    pre = raw[:matches[0].start()].strip()
    if pre:
        turns.append(("user", pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        turns.append((ROLE_WORDS.get(m.group(1).lower(), "user"), raw[m.end():end].strip()))
    return _finish(turns, raw) or Dialog(raw, [], raw)


def clamp_dialog(d: Dialog, limit: int) -> Dialog:
    """Кламп по символам с сохранением ХВОСТА: последняя реплика целиком, история — свежие реплики."""
    q = d.question if len(d.question) <= limit else d.question[-limit:]
    budget = max(0, limit - len(q))
    kept: list[tuple[str, str]] = []
    for role, text in reversed(d.history):
        if budget <= 60:
            break
        t = text[-min(len(text), budget):] if len(text) > budget else text
        kept.append((role, t))
        budget -= len(t)
    kept.reverse()
    return Dialog(q, kept, d.raw)


def render_history(d: Dialog, max_chars: int = 1600, per_turn: int = 420) -> str:
    lines, total = [], 0
    for role, text in reversed(d.history[-6:]):
        line = f"{'Пользователь' if role == 'user' else 'Ассистент'}: {truncate(text, per_turn)}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(reversed(lines))


def is_dependent(d: Dialog) -> bool:
    """Последняя реплика не самодостаточна и зависит от истории."""
    if not d.history:
        return False
    q = normalize_text(d.question).lower()
    toks = tokenize(d.question)
    content = [t for t in toks if not t[0].isdigit()]
    words = set(re.findall(r"[а-яё]+", q))
    if _FOLLOW_START.match(q) and len(content) <= 8:
        return True
    if len(content) <= 4:
        return True
    if words & _ANAPHORA or any(w.startswith(tuple(_ANAPHORA_STEMS)) for w in words):
        return len(content) <= 10
    return False


_REF_RE = re.compile(
    r"(?:ст\.?|статья|статье|статьи)\s*\d+(?:\.\d+)?(?:\s*(?:ТК|НК|КоАП|ГК)\s*РФ)?"
    r"|№\s*[А-ЯЁ]{1,3}-\d+(?:/\d{4})?|(?<![\w-])[А-ЯЁ]{1,3}-\d+(?:/\d{4})?(?![\w])"
    r"|\d+-ФЗ|(?:ТК|НК|КоАП|ГК)\s*РФ", re.U)


def history_refs(d: Dialog, limit: int = 4) -> list[str]:
    seen: list[str] = []
    for role, text in reversed(d.history):
        for m in _REF_RE.finditer(text):
            r = m.group().strip()
            if r not in seen:
                seen.append(r)
    return seen[:limit]


def retrieval_query(d: Dialog) -> tuple[str, str]:
    """→ (query, context). context добавляется в запрос только для зависимых реплик."""
    if not is_dependent(d):
        refs = [r for r in history_refs(d) if r not in d.question] if d.history else []
        # даже самодостаточный вопрос выигрывает от явных реквизитов из истории,
        # если сам их не содержит
        return d.question, (" ".join(refs) if refs and not _REF_RE.search(d.question) else "")
    prev = d.prev_user[-1] if d.prev_user else (d.history[-1][1] if d.history else "")
    refs = " ".join(r for r in history_refs(d) if r not in prev)
    return d.question, truncate(f"{prev} {refs}", 500)


_REWRITE_CACHE = LRU(512)
REWRITE_SYSTEM = ("Ты переформулируешь последнюю реплику пользователя в самостоятельный вопрос, "
                  "понятный без истории диалога. Подставь вместо местоимений и отсылок конкретные "
                  "документы, статьи, номера из истории. Ничего не отвечай и не добавляй новых фактов. "
                  "Выведи ТОЛЬКО текст вопроса, одной строкой.")


def rewrite_followup(client: LlmClient, d: Dialog, deadline: float) -> Optional[str]:
    key = d.raw[-1500:]
    cached = _REWRITE_CACHE.get(key)
    if cached is not None:
        return cached or None
    hist = render_history(d, 1200)
    try:
        out = client.chat(
            [{"role": "system", "content": REWRITE_SYSTEM},
             {"role": "user", "content": f"История:\n{hist}\n\nПоследняя реплика: {d.question}\n\nСамостоятельный вопрос:"}],
            max_tokens=160, temperature=0.0, deadline=deadline, retries=1)
    except ApiError:
        return None
    out = " ".join(out.split()).strip("«»\"' ")
    if not out or len(out) > 600:
        _REWRITE_CACHE.put(key, "")
        return None
    _REWRITE_CACHE.put(key, out)
    return out
