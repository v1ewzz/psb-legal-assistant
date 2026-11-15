# -*- coding: utf-8 -*-
"""Guard v2: парсер диалога (§10.1), сущности из истории, эвристический
standalone-rewrite для retrieval; Analyze-lite (LLM) вызывает pipeline.

Правила §10.1:
- диалог = ячейка с префиксами `Пользователь:` / `Ассистент:` (регистр/пробелы
  терпим); одиночный вопрос — всё остальное;
- отвечаем на последнюю ПОЛЬЗОВАТЕЛЬСКУЮ реплику; если последняя реплика
  вообще не пользовательская — аномалия, берём последнюю пользовательскую;
- реплики Ассистента — не источник фактов, но как retrieval-запрос годятся
  (это наши же уточняющие вопросы);
- ролевые префиксы внутри одиночного вопроса → fake_system-флаг
  (обрабатывается промптом, не ломая конвейер).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingest import QUERY_REF_RE

ROLE_RE = re.compile(r"^\s*(пользователь|юзер|user|ассистент|assistant|бот|bot)\s*:\s*",
                     re.I)
USER_ROLE_RE = re.compile(r"^\s*(пользователь|юзер|user)\s*:", re.I)
ASSISTANT_ROLE_RE = re.compile(r"^\s*(ассистент|assistant|бот|bot)\s*:", re.I)

# реквизиты и компании для подтягивания из истории (§3.4, §9.4)
DOC_NUM_RE = re.compile(r"(?:№\s*)?\b([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)\b")
QUOTED_NAME_RE = re.compile(r"«([^»]{3,40})»")
CONFIRM_RE = re.compile(
    r"^\s*(да[.,!]?\s|именно|верно|так\sи\sесть|конечно|точно)\b", re.I)
FAKE_SYSTEM_RE = re.compile(
    r"^\s*(system|систем[ауы]|системное сообщение|assistant:)\s*:", re.I | re.M)


@dataclass
class Dialog:
    """Разобранная ячейка вопроса (§10.1)."""
    raw: str
    is_dialog: bool = False
    last_user: str = ""                    # последняя пользовательская реплика
    prev_user: str = ""                    # предыдущая пользовательская реплика
    history: list = field(default_factory=list)   # [(role, text)] до last_user
    entities: list = field(default_factory=list)  # реквизиты/компании из диалога
    fake_system: bool = False
    anomaly: str = ""


def parse_dialog(raw: str) -> Dialog:
    """Разрезать ячейку по ролевым префиксам; выделить последний вопрос."""
    raw = (raw or "").strip()
    if not raw:
        return Dialog(raw="", last_user="")
    lines = raw.splitlines()
    has_roles = any(ROLE_RE.match(ln) for ln in lines)
    if not has_roles:
        # одиночный вопрос; ролевой префикс ВНУТРИ = потенциальная инъекция
        fake = bool(FAKE_SYSTEM_RE.search(raw))
        return Dialog(raw=raw, is_dialog=False, last_user=raw,
                      entities=_extract_entities(raw), fake_system=fake)

    turns: list[list] = []   # [role, [lines]]
    for ln in lines:
        m = ROLE_RE.match(ln)
        if m:
            role = "user" if USER_ROLE_RE.match(ln) else "assistant"
            turns.append([role, [ln[m.end():].strip()]])
        elif turns:
            turns[-1][1].append(ln.strip())
        else:
            # текст до первой реплики — считаем пользовательским контекстом
            turns.append(["user", [ln.strip()]])
    turns = [[role, "\n".join(parts).strip()] for role, parts in turns if
             "\n".join(parts).strip()]

    last_user_idx = max((i for i, (role, _) in enumerate(turns)
                         if role == "user"), default=-1)
    anomaly = ""
    if last_user_idx == -1:
        return Dialog(raw=raw, is_dialog=True, last_user=raw, anomaly="no_user_turn")
    if last_user_idx != len(turns) - 1:
        anomaly = "last_not_user"
    prev_user = ""
    for i in range(last_user_idx - 1, -1, -1):
        if turns[i][0] == "user":
            prev_user = turns[i][1]
            break
    history = [(role, text) for role, text in turns[:last_user_idx]]
    last_user = turns[last_user_idx][1]
    all_text = "\n".join(text for _, text in turns)
    return Dialog(raw=raw, is_dialog=True, last_user=last_user, prev_user=prev_user,
                  history=history, entities=_extract_entities(all_text),
                  anomaly=anomaly)


def _extract_entities(text: str) -> list[str]:
    """Реквизиты и названия компаний из диалога (для standalone-запроса)."""
    entities: list[str] = []
    for m in DOC_NUM_RE.finditer(text):
        num = m.group(1)
        if num not in entities:
            entities.append(num)
    for m in QUOTED_NAME_RE.finditer(text):
        name = m.group(1).strip()
        if name.lower() in {"меридиан"}:      # наша компания — не различает вопрос
            continue
        if name not in entities and len(name.split()) <= 3:
            entities.append(name)
    return entities


def standalone_query(dialog: Dialog) -> str:
    """Последняя реплика + сущности из истории — запрос для реранка."""
    q = dialog.last_user
    missing = [e for e in dialog.entities
               if e.lower() not in q.lower()]
    if missing:
        q = q + " (" + "; ".join(missing) + ")"
    return q


_STOPWORDS = {
    "какой", "какая", "какие", "какого", "какому", "сколько", "почему",
    "когда", "где", "куда", "нужно", "нужно", "надо", "можно", "нельзя",
    "есть", "быть", "будет", "если", "чтобы", "должен", "должна", "требуется",
    "вопрос", "прошу", "скажите", "расскажите", "объясните", "согласно",
    "по", "о", "об", "для", "на", "в", "с", "и", "а", "но", "или", "то",
    "это", "тем", "чем", "его", "ее", "их", "наш", "наша", "нашего",
    "компании", "компания", "предусмотрен", "предусмотрено", "составляет",
}


def topic_of(text: str) -> str:
    """2 самых длинных содержательных слова вопроса — для отказа с темой:
    «В базе знаний нет документов о <X>…» вместо безликого «не удалось найти»."""
    words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{3,}", text or "")
    good = [w.lower() for w in words if w.lower() not in _STOPWORDS]
    if not good:
        return "запрошенной теме" if text else "вопросе"
    top = set(sorted(good, key=len, reverse=True)[:2])
    # порядок слов — как в вопросе («пособия беременности», не «беременности пособия»)
    return " ".join(w for w in good if w in top)


def heuristic_queries(dialog: Dialog) -> list[str]:
    """Эвристические retrieval-запросы без LLM (§10.1.5 упрощённо).

    q1 — последняя реплика + сущности из истории, которых в ней нет;
    q2 — то же + предыдущие пользовательские реплики (контекст «у них
         компьютеры покупали»);
    +q3 — последняя реплика Ассистента, если ответ — короткое подтверждение
         («Да.»): уточнение Ассистента формулирует тему.
    """
    q1 = standalone_query(dialog)
    queries = [q1]
    ctx_parts = []
    if dialog.prev_user and dialog.prev_user.lower() not in q1.lower():
        ctx_parts.append(dialog.prev_user)
    if CONFIRM_RE.match(dialog.last_user):
        for role, text in reversed(dialog.history):
            if role == "assistant" and text:
                ctx_parts.append(text)
                break
    if ctx_parts:
        q2 = q1 + " " + " ".join(ctx_parts)
        if q2 not in queries:
            queries.append(q2)
    return queries


def detect_injection_in_question(raw: str) -> bool:
    """Инъекция в самом вопросе (T1-строки, вставленные пользователем)."""
    from ingest import is_compromised
    return any(is_compromised(ln) for ln in (raw or "").splitlines())
