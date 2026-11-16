# -*- coding: utf-8 -*-
"""Текстовые утилиты: нормализация, токенизация со стеммингом, числа, даты.

Только stdlib. Используется индексацией, поиском и верификацией, поэтому
токенизация запроса и документа строго одинакова.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable, Optional

_HOMOGLYPHS = str.maketrans({
    "ё": "е", "Ё": "Е",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u200b": "",
    "–": "-", "—": "-", "−": "-", "‑": "-",
})


def normalize_text(text: str) -> str:
    """ё→е, латинские гомоглифы→кириллица, неразрывные пробелы, тире."""
    return (text or "").translate(_HOMOGLYPHS)


STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли
если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб
без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой
совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех
никогда можно при наконец два об другой хоть после над больше тот через эти нас
про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда
лучше чуть том нельзя такой им более всегда конечно всю между это также либо
какие какое какой каком каких какую какого какому какими сколько каков какова каковы
нужно нужна нужен нужны можно должен должна должны ли ответь скажите подскажите расскажите
""".split())

_SUFFIXES = tuple(sorted({
    "иями", "ями", "ами", "иях", "ого", "его", "ому", "ему", "ыми", "ими", "ией",
    "ать", "ять", "ить", "еть", "ует", "уют", "ает", "ают", "яет", "яют", "ила",
    "ыла", "ена", "ано", "ены", "аны", "ится", "ются", "ется", "ется",
    "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ие", "ые", "ых", "их", "ую", "юю",
    "ом", "ем", "ам", "ям", "ах", "ях", "ов", "ев", "ей", "ию", "ия", "ье", "ья",
    "ью", "ть", "ет", "ут", "ют", "ит", "ат", "ят", "ла", "ли", "ло", "ны", "но",
    "ы", "и", "а", "я", "у", "ю", "е", "о", "ь", "й",
}, key=lambda s: (-len(s), s)))

_CYR = re.compile(r"^[а-я]+$")
TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)+|\w+", re.U)


def stem(word: str) -> str:
    """Лёгкий стеммер для русского: срез самого длинного окончания, основа ≥ 3."""
    if len(word) <= 4 or not _CYR.match(word):
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


def tokenize(text: str, *, drop_stop: bool = True) -> list[str]:
    """Нормализация → нижний регистр → токены (числа «3.1» целиком) → стемминг."""
    out: list[str] = []
    for tok in TOKEN_RE.findall(normalize_text(text).lower()):
        tok = tok.replace(",", ".")
        if tok in STOPWORDS and drop_stop:
            continue
        if tok.isdigit() or "." in tok:
            out.append(tok)
            if "." in tok:  # «3.1» ищется и как «3.1», и как «3», «1»
                out.extend(p for p in tok.split(".") if p)
        else:
            out.append(stem(tok))
    return out


def content_stems(text: str) -> set[str]:
    """Смысловые основы (без чисел) — для лексической сверки утверждений."""
    return {t for t in tokenize(text) if not t[0].isdigit() and len(t) > 2}


# ---------- числа ----------

_NUM_WORDS = {
    "ноль": 0, "один": 1, "одного": 1, "одной": 1, "одному": 1, "два": 2, "двух": 2, "две": 2,
    "три": 3, "трех": 3, "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6,
    "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10, "одиннадцать": 11, "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15,
    "пятнадцати": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "восемнадцати": 18, "девятнадцать": 19, "двадцать": 20, "двадцати": 20,
    "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40, "пятьдесят": 50,
    "пятидесяти": 50, "шестьдесят": 60, "шестидесяти": 60, "семьдесят": 70,
    "восемьдесят": 80, "девяносто": 90, "сто": 100, "ста": 100,
    "тысяча": 1000, "тысячи": 1000, "тысяч": 1000,
}
_NUM_RE = re.compile(r"(?<![\w.])\d{1,3}(?:[ ]\d{3})+(?:[.,]\d+)?|(?<![\w.])\d+(?:[.,]\d+)?")


def canon_number(raw: str) -> str:
    """«450 000» → «450000», «0,10» → «0.1», «007» → «7»."""
    s = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    s = s.lstrip("0") or "0"
    if s.startswith("."):
        s = "0" + s
    return s


def extract_numbers(text: str, *, with_words: bool = True) -> set[str]:
    """Канонические числа из текста: цифрами и (опционально) прописью."""
    t = normalize_text(text)
    out = {canon_number(m.group()) for m in _NUM_RE.finditer(t)}
    if with_words:
        for w in re.findall(r"[а-я]+", t.lower()):
            if w in _NUM_WORDS:
                out.add(str(_NUM_WORDS[w]))
    return out


DAY_KINDS = (("календар", "кал"), ("рабоч", "раб"))
_NUM_UNIT_RE = re.compile(
    r"(\d{1,3}(?:[ ]\d{3})+|\d+(?:[.,]\d+)?)\s*(?:\([^)]{0,80}\)\s*)?"
    r"(календарн\w*|рабоч\w*)\s+(?:дн|сут)", re.I)


def number_daykind_pairs(text: str) -> set[tuple[str, str]]:
    """{(число, 'кал'|'раб')}: «30 календарных дней», «5 (пяти) рабочих дней»."""
    out = set()
    for m in _NUM_UNIT_RE.finditer(normalize_text(text)):
        kind = "кал" if m.group(2).lower().startswith("календ") else "раб"
        out.add((canon_number(m.group(1)), kind))
    return out


# ---------- даты ----------

_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
           "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
           "декабря": 12}
_DATE_TXT = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)
_DATE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def parse_dates(text: str) -> list[date]:
    """Все полные даты из текста («1 марта 2025», «01.03.2025»)."""
    out: list[date] = []
    t = normalize_text(text)
    for m in _DATE_TXT.finditer(t):
        try:
            out.append(date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))))
        except ValueError:
            pass
    for m in _DATE_NUM.finditer(t):
        try:
            out.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            pass
    return out


def first_date(text: str) -> Optional[date]:
    ds = parse_dates(text)
    return ds[0] if ds else None


def years_in(text: str) -> list[int]:
    return [int(y) for y in re.findall(r"\b(20\d{2}|19\d{2})\b", text)]


def sha1(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def one_line(text: str) -> str:
    return " ".join((text or "").split())


def truncate(text: str, limit: int) -> str:
    text = one_line(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def iter_lines(text: str) -> Iterable[str]:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            yield line
