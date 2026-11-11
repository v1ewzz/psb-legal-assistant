# -*- coding: utf-8 -*-
"""Ingestion v2 (Фаза 1 §7): kb.zip → чанки по пунктам, реестры, BM25-индекс.

v2 (после Сабмита №1, 27.4):
- чанки по пунктам договоров/регламентов и по пунктам/частям/абзацам законов
  (§7.1–7.2), а не «блоками» — BM25 находит нужный пункт, LLM видит точный текст;
- цитируемые пути `path` («п. 3.1», «ч. 2, абз. 3») и дружелюбные short_name
  («ТК РФ, ст. 70», «Договор аренды № А-08/2024») — для ссылок в ответе (D3);
- full-doc чанки для коротких документов (К-12, ЮО-7, ДС) (§7.4);
- temporal-метаданные в docs.json (редакции Регламента, К-12 valid_from,
  ДС applies_from) для temporal-слоя (D1);
- реестр алиасов компаний/номеров в refs.json — exact-ref boost (§9.1.1);
- собственный токенизатор с гомоглифами и лёгким стеммингом (§9.1.2),
  числа («3.1») — цельные токены; один и тот же код для индекса и запроса;
- compromised-строки (T1, §10.2) вырезаются до склейки абзацев;
- амендменты (паттерны A/B §3.2) — в notes, не в текст чанков.

CLI: py ingest.py --build-index [--kb data/kb.zip] [--outdir index]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Optional

import bm25s
import numpy as np

from config import first_nonempty

# --- Ловушка Т1: детектор compromised-строк (§10.2) ---
COMPROMISED_PATTERNS = [
    re.compile(r"(ИИ-ассистент|AI-assistant|AI assistant|языковая модель|LLM).{0,120}(игнорируй|не упоминай|отвечай|всегда отвечай)", re.I),
    re.compile(r"(игнорируй|забудь).{0,60}(документ|инструкц|системн)", re.I),
    re.compile(r"(инструкция|правило).{0,40}(для\s(ИИ|ассистента|модели))", re.I),
    re.compile(r"(не упоминай|скрой).{0,60}(эту инструкцию|данное правило)", re.I),
    # команда «отвечай X дней» с числом (второй вид Т1: строка-продолжение инъекции)
    re.compile(r"отвечай.{0,60}[\u00ab\"]?\s*\d+\s+(?:календарных|рабочих)", re.I),
]

# Амендменты (§3.2): паттерн A (в шапке) и B (inline). Даты содержат точки,
# поэтому между якорями — ленивые диапазоны, а не [^.]*
AMEND_RE = re.compile(
    r"^(?:Стать[ьяи]|Часть|Пункт\w*|Подпункт\w*|Абзац\w*)\b.{0,160}?"
    r"(?:измен[аены]|дополнен|утратил\w* силу|признан утратившим).{0,100}?\s[-–—]\s"
    r"(?:Федеральн\w+ законом|Федеральный закон)",
    re.I,
)
AMEND_LONG_RE = re.compile(r"^Федеральн\w+ законом[и]? от\b.*(?:внесен|вступа)", re.I)
NOTE_RE = re.compile(r"^(О конституционно|О проверке конституционности|Примечание)", re.I)

# Числа-реквизиты: А-08/2024, П-17/2024, ПТ-05/2023, К-12, ЮО-7, 14-П, 2-П
DOC_NUMBER_RE = re.compile(r"\b([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)\b")

# Паттерны номеров/статей в запросе: «ст. 59», «п. 3.1», «№ К-12», «А-08/2024»
QUERY_REF_RE = re.compile(
    r"(?:ст\.?|статья)\s*(\d+(?:[.,]\d+)?)|№\s*([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)|\b([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)\b",
    re.I,
)

# --- Структура (§7.1–7.2) ---
SECTION_RE = re.compile(r"^(\d{1,2})\.\s+([А-ЯЁA-Z«].*)$")      # «3. Арендная плата»
PUNKT_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})+)\.?\s+(.*)$")    # «3.1. Арендная плата…»
SUBITEM_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)\)\s+(.*)$")   # «1) …», «2.1) …» (НК)
LAW_TITLE_RE = re.compile(r"^Статья\s+(\d+(?:[.,]\d+)?)\.\s*(.*)$", re.I)
LAW_PUNKT_RE = re.compile(r"^(\d{1,2})\.\s+(.+)$")   # одноуровневые пункты законов
HEAD_KEY_RE = re.compile(r"^(Источник|URL|Дата выгрузки):", re.I)
QUOTED_PUNKT_RE = re.compile(r"^«\s*(\d{1,2}(?:\.\d{1,2})+)\.?\s+(.*)»?\.?»?$")

LAW_CODE_MAP = [("ТК РФ", "ТК РФ"), ("НК РФ", "НК РФ"), ("КоАП РФ", "КоАП РФ")]
FZ_RE = re.compile(r"N\s*(\d{2,3}-ФЗ)")
FULL_DOC_MAX = 4500          # полнотекстовый чанк для коротких документов (§7.4)
TARGET_CHUNK_MAX = 900
MIN_CHUNK_CHARS = 40

TOKENIZER_ID = "custom_ru_v2"

# Токенизация (§9.1.2): ё→е, гомоглифы, лёгкий стемминг хвостов, числа целиком
HOMOGLYPH_TABLE = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У", "a": "а",
    "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
})
SUFFIXES_LONG = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
                 "ой", "ый", "ий", "ая", "ое", "ые", "их", "ых",
                 "ов", "ев", "ей", "ам", "ям", "ах", "ях", "ья", "ье", "ия", "ие",
                 "ию", "ом", "ем", "ым", "им", "ются", "ется", "ться",
                 "ли", "ла", "ло", "ет", "ут", "ют", "ат", "ят", "ся")
SUFFIXES_SHORT = ("а", "я", "ы", "и", "у", "ю", "е", "ь", "й")

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RU_DATE_NUM_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b")
RU_DATE_TEXT_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(RU_MONTHS) + r")\s+(\d{4})\s*(?:г\.?|года)?", re.I)


def normalize_text(text: str) -> str:
    """ё→е + гомоглифы (латиница→кириллица, T9 §9.1) — для сравнения строк."""
    return text.translate(HOMOGLYPH_TABLE).replace("ё", "е").replace("Ё", "Е")


def tokenize_ru(text: str) -> list[str]:
    """Токены для BM25 (§9.1.2). Числа «3.1» — цельные токены; хвосты слов
    усекаются только у слов длиннее 4 символов. ЕДИНЫЙ код для индексации
    и запроса — иначе BM25 ломается."""
    tokens: list[str] = []
    for m in re.finditer(r"\d+(?:[.,]\d+)?|[а-яёa-z]+", normalize_text(text).lower()):
        tok = m.group()
        if any(ch.isdigit() for ch in tok):
            tokens.append(tok.replace(",", "."))
            continue
        if len(tok) > 5:
            for suf in SUFFIXES_LONG:
                if tok.endswith(suf) and len(tok) - len(suf) >= 4:
                    tok = tok[: len(tok) - len(suf)]
                    break
        if len(tok) > 4:
            for suf in SUFFIXES_SHORT:
                if tok.endswith(suf) and len(tok) - len(suf) >= 4:
                    tok = tok[: len(tok) - len(suf)]
                    break
        tokens.append(tok)
    return tokens


def parse_ru_date(text: str) -> Optional[date]:
    """Первая дата в тексте: «15.04.2025», «1 октября 2024 г.» → date | None."""
    m = RU_DATE_NUM_RE.search(text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    m = RU_DATE_TEXT_RE.search(text)
    if m:
        try:
            return date(int(m.group(3)), RU_MONTHS[m.group(2).lower()], int(m.group(1)))
        except (ValueError, KeyError):
            pass
    return None


def norm_doc_number(raw: str) -> str:
    """«А-08-2024» → «А-08/2024»; «П-17-2024» → «П-17/2024» (стиль эталона)."""
    return re.sub(r"^([А-ЯЁ]{1,2}-\d+)-(\d{4})$", r"\1/\2", raw.strip())


def is_compromised(line: str) -> bool:
    """T1: строка-инъекция внутри документа не должна попасть в чанк-текст."""
    return any(p.search(line) for p in COMPROMISED_PATTERNS)


def read_docx(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as doc:
        xml = doc.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n".join(
        "".join(x.text or "" for x in paragraph.iter(ns + "t"))
        for paragraph in root.iter(ns + "p")
    )


def load_documents(zip_path: Path) -> list[tuple[str, str]]:
    """[(file_name, text)] из kb.zip (по мотивам стартера)."""
    documents = []
    with zipfile.ZipFile(zip_path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or name.startswith("__MACOSX/"):
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in {".txt", ".md", ".docx"}:
                continue
            raw = archive.read(name)
            try:
                text = read_docx(raw) if suffix == ".docx" else raw.decode("utf-8-sig")
            except (UnicodeDecodeError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError):
                print(f"Пропущен нечитаемый документ: {name}", file=sys.stderr)
                continue
            if text.strip():
                documents.append((name, text.strip()))
    if not documents:
        raise ValueError("В архиве базы знаний нет читаемых документов")
    return documents


def find_kb_zip(env: Optional[dict] = None) -> Optional[Path]:
    """kb.zip: DATA_DIR → script_dir/DATA_DIR → cwd → script_dir (§2.6.4)."""
    env = os.environ if env is None else env
    data_dir = Path(first_nonempty(env.get("DATA_DIR")) or "data")
    script_dir = Path(__file__).resolve().parent
    for root in (data_dir, script_dir / data_dir, Path.cwd(), script_dir):
        for name in ("kb.zip", "knowledge-base.zip", "knowledge_base.zip"):
            path = root / name
            if path.is_file():
                return path
    return None


def slugify_doc_id(file_name: str) -> str:
    stem = Path(file_name).stem
    stem = stem.replace(" ", "_")
    stem = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_.-]", "", stem)
    return stem[:80] or "doc"


def _split_long(text: str, limit: int = TARGET_CHUNK_MAX) -> list[str]:
    """Длинный текст → куски по предложениям, каждый ≤ ~limit (§7.4)."""
    if len(text) <= limit * 1.5:
        return [text]
    parts, piece, piece_len = [], [], 0
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if piece_len and piece_len + len(sentence) > limit:
            parts.append("\n".join(piece))
            piece, piece_len = [], 0
        piece.append(sentence)
        piece_len += len(sentence) + 1
    if piece:
        parts.append("\n".join(piece))
    return parts


def _mk_chunk(doc_id: str, short_name: str, path: str, lead_in: str, text: str,
              counter: dict) -> list[dict]:
    """Чанк с цитируемым path; длинный текст режется, path получает «(фрагмент k)»."""
    text = text.strip()
    if not text or len(text) < MIN_CHUNK_CHARS:
        return []
    pieces = _split_long(text)
    out = []
    for k, piece in enumerate(pieces, 1):
        counter["n"] += 1
        piece_path = path if len(pieces) == 1 else f"{path} (фрагмент {k})"
        out.append({
            "chunk_id": f"{doc_id}_c{counter['n']:03d}",
            "doc_id": doc_id,
            "doc_type": "company" if "законы__" not in doc_id else "law",
            "short_name": short_name,
            "path": piece_path,
            "lead_in": lead_in.strip()[:160],
            "text": piece.strip(),
            "text_ctx": f"{short_name}, {piece_path}. {piece.strip()}",
        })
    return out


# --------------------------------------------------------------------------
# Законы (§7.1)
# --------------------------------------------------------------------------

def parse_law(doc_id: str, file_name: str, text: str) -> tuple[dict, list[dict], list[str]]:
    """Закон → (meta, chunks, notes). Пункты НК/КоАП; части/абзацы ТК (§7.1)."""
    stem = Path(file_name).stem
    lines = [ln for ln in text.splitlines()]
    head_lines = [ln.strip() for ln in lines[:5] if HEAD_KEY_RE.match(ln.strip())]
    source = next((ln for ln in head_lines if ln.lower().startswith("источник")), "")
    source = re.sub(r"^Источник:\s*", "", source, flags=re.I)

    code = None
    for needle, name in LAW_CODE_MAP:
        if needle in source or needle in stem:
            code = name
            break
    if code is None:
        fz = FZ_RE.search(source)
        code = fz.group(1) if fz else "ФЗ"

    art_m = re.search(r"ст(\d+(?:[.,]\d+)?)", stem, re.I)
    article = art_m.group(1).replace(",", ".") if art_m else ""
    short_name = f"{code}, ст. {article}" if article else code

    amendments = [ln.strip() for ln in lines
                  if AMEND_RE.match(ln.strip()) and " - " in ln]
    amendment_dates = sorted({d.isoformat() for d in
                              (parse_ru_date(a) for a in amendments) if d})

    body_start = 0
    for i, ln in enumerate(lines):
        if LAW_TITLE_RE.match(ln.strip()):
            body_start = i   # строка «Статья N. …» — тоже контент (МРОТ: это сама норма)
            break

    chunks: list[dict] = []
    notes: list[str] = [ln.strip() for ln in lines
                        if AMEND_LONG_RE.match(ln.strip()) or NOTE_RE.match(ln.strip())]
    counter = {"n": 0}
    part_no, abs_no = 0, 0
    lead_in = ""       # вводная строка списка («…заключается:») — вшивается в элементы
    buf: list[str] = []
    buf_path = ""

    def close() -> None:
        nonlocal buf
        if buf:
            chunks.extend(_mk_chunk(doc_id, short_name, buf_path, lead_in,
                                    "\n".join(buf), counter))
            buf = []

    for ln in lines[body_start:]:
        stripped = ln.strip()
        if not stripped or stripped == "\u00a0":
            continue
        if AMEND_RE.match(stripped) or AMEND_LONG_RE.match(stripped) or NOTE_RE.match(stripped):
            close()
            notes.append(stripped)
            continue
        if is_compromised(stripped):
            close()
            notes.append("compromised: " + stripped)
            continue
        pm = PUNKT_RE.match(stripped)
        lm = LAW_PUNKT_RE.match(stripped)
        sm = SUBITEM_RE.match(stripped)
        if pm or lm:                             # «1. Налоговая ставка…» (НК, КоАП)
            close()
            lead_in = ""
            number = (pm or lm).group(1).rstrip(".")
            buf_path = f"п. {number}"
            buf = [stripped]
            continue
        if sm:                                   # «1) …», «2.1) …» (подпункты)
            close()
            lead_in = ""
            buf_path = f"пп. {sm.group(1).rstrip('.')}"
            buf = [stripped]
            continue
        if stripped.endswith(":"):               # lead_in нового списка = новая часть
            close()
            part_no += 1
            lead_in = stripped
            abs_no = 0
            buf_path = f"ч. {part_no}"
            continue
        if lead_in:                              # элемент списка: lead_in вшивается
            abs_no += 1
            close()
            buf_path = f"ч. {part_no}, абз. {abs_no}"
            buf = [f"{lead_in} {stripped}"]
            # пункт, завершающийся точкой, — последний элемент списка (юридический
            # стиль «; … ; … .»): далее идут самостоятельные абзацы без lead_in
            if re.search(r"[.)]»?\.?$", stripped) and not stripped.endswith(";"):
                lead_in = ""
            continue
        abs_no += 1
        close()
        buf_path = f"абз. {abs_no}"
        buf = [stripped]
    close()

    meta = {
        "doc_id": doc_id,
        "file_name": file_name,
        "doc_type": "law",
        "kind": "law",
        "short_name": short_name,
        "code": code,
        "article": article,
        "doc_date": None,
        "valid_from": amendment_dates[0] if amendment_dates else None,
        "valid_to": None,
        "future_editions": amendment_dates,      # «изменена с …» из шапки (T11)
        "pull_date": "2026-10-10",
        "storage_numbers": sorted(set(
            [article] + [m.group(1) for m in DOC_NUMBER_RE.finditer(source)])),
    }
    return meta, chunks, notes


# --------------------------------------------------------------------------
# Документы компании (§7.2)
# --------------------------------------------------------------------------

KIND_PATTERNS = [
    ("supplement", "доп соглашение"),
    ("regulation", "регламент"),
    ("letter", "письмо"),
    ("memo", "служебная записка"),
    ("contract", "договор"),   # последним: «Регламент … договоров» содержит «договор»
]


def detect_kind(stem: str) -> str:
    low = stem.lower()
    for kind, needle in KIND_PATTERNS:
        if needle in low:
            return kind
    return "company_doc"


def parse_company(doc_id: str, file_name: str, text: str) -> tuple[dict, list[dict], list[str]]:
    """Документ компании → (meta, chunks, notes). Пункты, шапка, full-doc."""
    stem = Path(file_name).stem
    kind = detect_kind(stem)
    lines = text.splitlines()

    parties = sorted(set(re.findall(r"ООО\s*«([^»]+)»", text)))
    numbers = sorted({norm_doc_number(m.group(1)) for m in DOC_NUMBER_RE.finditer(stem)})
    for ln in lines[:12]:
        for m in DOC_NUMBER_RE.finditer(ln):
            numbers.append(norm_doc_number(m.group(1)))
    numbers = sorted(set(numbers))

    # --- short_name в стиле эталона (§4.3) ---
    title_m = re.search(r"№\s*([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)", lines[0] if lines else "")
    number = norm_doc_number(title_m.group(1)) if title_m else (numbers[0] if numbers else "")
    if kind == "contract":
        counterparty = next((p for p in parties if p != "Меридиан"), "")
        short_name = f"Договор № {number}" if number else "Договор"
        kind_word = "аренды" if "аренд" in stem.lower() else "поставки"
        short_name = f"Договор {kind_word} № {number}"
        if counterparty:
            short_name += f" с ООО «{counterparty}»"
    elif kind == "supplement":
        short_name = f"Дополнительное соглашение № 1 к договору № {number or 'П-17/2024'}"
    elif kind == "letter":
        short_name = f"Письмо комплаенс-службы № {number}"
        d = parse_ru_date(text[:400])
        if d:
            short_name += f" от {d.strftime('%d.%m.%Y')}"
    elif kind == "regulation":
        ed_m = re.search(r"Редакция от (\d{2}\.\d{2}\.\d{4})", text[:600])
        edition = ed_m.group(1) if ed_m else ""
        short_name = "Регламент согласования договоров"
        if edition:
            short_name += f" (ред. от {edition}"
            if "утратил" in text[:600].lower():
                short_name += ", утратил силу"
            short_name += ")"
    elif kind == "memo":
        short_name = f"Служебная записка № {number or 'ЮО-7'}"
        d = parse_ru_date(text[:400])
        if d:
            short_name += f" от {d.strftime('%d.%m.%Y')}"
    else:
        short_name = stem.split("__", 1)[-1]

    # --- temporal-метаданные (§6.1) ---
    meta = {
        "doc_id": doc_id,
        "file_name": file_name,
        "doc_type": "company",
        "kind": kind,
        "short_name": short_name,
        "number": number,
        "parties": parties,
        "doc_date": None,
        "valid_from": None,
        "valid_to": None,
        "edition": None,
        "applies_from": None,
        "amends_doc": None,
        "storage_numbers": numbers,
    }
    head_text = "\n".join(lines[:10])
    meta["doc_date"] = (parse_ru_date(head_text).isoformat()
                        if parse_ru_date(head_text) else None)
    if kind == "regulation":
        ed_m = re.search(r"Редакция от (\d{2}\.\d{2}\.\d{4})", text[:600])
        if ed_m:
            meta["edition"] = parse_ru_date(ed_m.group(1)).isoformat()
            meta["valid_from"] = meta["edition"]
        if "утратил" in text[:600].lower():
            meta["status"] = "revoked"
        else:
            meta["status"] = "active"
        # ред. 2023 утратила силу 01.03.2025 — дата в шапке ред. 2025; заполним в build_index
    if kind == "letter":
        m = re.search(r"[Сс]\s+(\d{1,2}\s+(?:" + "|".join(RU_MONTHS) + r")\s+\d{4})", text)
        if m:
            d = parse_ru_date(m.group(1))
            meta["valid_from"] = d.isoformat() if d else None
    if kind == "supplement":
        # дата подписания ДС — строка «г. Город — D», не дата базового договора
        signed = None
        for ln in lines[:8]:
            if "—" in ln or "--" in ln:
                signed = parse_ru_date(ln)
                if signed:
                    break
        if signed is not None:
            meta["signed"] = signed.isoformat()
            meta["doc_date"] = meta["signed"]
        else:
            meta["signed"] = meta["doc_date"]
        m = re.search(r"подписаны после\s+(\d{1,2}\s+(?:" + "|".join(RU_MONTHS)
                      + r")\s+\d{4}|\d{2}\.\d{2}\.\d{4})", text)
        if m:
            d = parse_ru_date(m.group(1))
            meta["applies_from"] = d.isoformat() if d else None
        am = re.search(r"к\s+договор[уа]\s+(?:поставки\s+)?№\s*([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)",
                       text[:600])
        if am:
            meta["amends_doc"] = norm_doc_number(am.group(1))

    # --- чанки ---
    chunks: list[dict] = []
    notes: list[str] = []
    counter = {"n": 0}
    section_title = ""
    body_lines: list[str] = []
    header_lines: list[str] = []   # строки до первого пункта/раздела → «шапка»
    # шапку выделяем только у структурных документов; у писем/служебок/ДС
    # весь текст идёт абзацами (+ есть full-doc чанк)
    structured = kind in {"contract", "regulation"}
    in_header = structured
    abs_no = 0
    buf: list[str] = []
    buf_path = ""
    lead_in = ""

    def close() -> None:
        nonlocal buf
        if buf:
            chunks.extend(_mk_chunk(doc_id, short_name, buf_path, lead_in,
                                    "\n".join(buf), counter))
            buf = []

    for ln in lines:
        stripped = ln.strip()
        if not stripped or stripped == "\u00a0":
            continue
        if is_compromised(stripped):        # T1: до склейки абзацев (§10.2)
            close()
            notes.append("compromised: " + stripped)
            continue
        if HEAD_KEY_RE.match(stripped):
            continue
        body_lines.append(stripped)
        pm = PUNKT_RE.match(stripped)
        qm = QUOTED_PUNKT_RE.match(stripped)   # «3.2. Товар доставляется…» в ДС
        sm = SECTION_RE.match(stripped) if not pm and not qm else None
        if in_header and not (pm or qm or sm):
            header_lines.append(stripped)
            continue
        in_header = False
        if qm:
            close()
            lead_in = ""
            buf_path = f"п. {qm.group(1).rstrip('.')}"
            buf = [f"п. {qm.group(1).rstrip('.')}. {qm.group(2)} (в редакции ДС)"]
            continue
        if pm:
            close()
            lead_in = ""
            buf_path = f"п. {pm.group(1).rstrip('.')}"
            buf = [stripped]
            continue
        if sm:
            close()
            section_title = sm.group(2).strip()
            lead_in = f"Раздел {sm.group(1)}. {section_title}."
            abs_no = 0
            continue
        abs_no += 1
        close()
        buf_path = f"абз. {abs_no}"
        buf = [stripped]
    close()

    # шапка документа (стороны, предмет, дата) — один обзорный чанк (§7.2.1);
    # отдельные абзацы из шапки НЕ индексируются — номер договора в них
    # забивал выдачу у диалоговых вопросов
    header_text = "\n".join(header_lines).strip()
    if header_text and len(header_text) >= MIN_CHUNK_CHARS:
        chunks.extend(_mk_chunk(doc_id, short_name, "шапка", "", header_text, counter))

    # full-doc чанк для коротких документов: письмо/служебка/ДС (§7.4);
    # у договоров/регламентов не делаем — съедают evidence-бюджет
    clean_body = "\n".join(ln for ln in body_lines if not is_compromised(ln))
    if kind in {"letter", "memo", "supplement"} and len(clean_body) <= FULL_DOC_MAX:
        chunks.append({
            "chunk_id": f"{doc_id}_full",
            "doc_id": doc_id,
            "doc_type": "company",
            "short_name": short_name,
            "path": "документ целиком",
            "lead_in": "",
            "text": clean_body,
            "text_ctx": f"{short_name}. {clean_body}",
        })

    return meta, chunks, notes


# --------------------------------------------------------------------------
# Сборка индекса
# --------------------------------------------------------------------------

def build_registry(doc_id: str, file_name: str, meta: dict) -> dict:
    """Запись docs.json (совместима с v1-полями head/storage_numbers)."""
    entry = dict(meta)
    entry["head"] = []
    return entry


def build_aliases(docs: list[dict]) -> list[dict]:
    """refs.doc_aliases: doc_id → строки-триггеры exact-ref boost (§9.1.1)."""
    out = []
    for meta in docs:
        aliases = {norm_doc_number(n) for n in meta.get("storage_numbers", [])}
        aliases |= {n for n in meta.get("storage_numbers", [])}
        if meta.get("doc_type") == "company":
            for p in meta.get("parties", []):
                if p != "Меридиан":
                    aliases.add(p)
        if meta.get("kind") == "letter":
            aliases.add("комплаенс")
        if meta.get("kind") == "memo":
            aliases.add("архив")
        out.append({"doc_id": meta["doc_id"],
                    "aliases": sorted(a for a in aliases if len(a) >= 3)})
    return out


def build_index(kb_zip: Path, outdir: Path) -> int:
    """Собрать все артефакты v2 в outdir; вернуть число чанков."""
    t0 = time.time()
    outdir.mkdir(parents=True, exist_ok=True)
    documents = load_documents(kb_zip)

    all_chunks: list[dict] = []
    docs_meta: list[dict] = []
    notes_by_doc: dict[str, list] = {}
    for file_name, text in documents:
        doc_id = slugify_doc_id(file_name)
        if Path(file_name).stem.startswith("законы__"):
            meta, chunks, notes = parse_law(doc_id, file_name, text)
        else:
            meta, chunks, notes = parse_company(doc_id, file_name, text)
        docs_meta.append(meta)
        notes_by_doc[doc_id] = notes
        all_chunks.extend(chunks)

    # кросс-заполнение: ред. 2023 действует до вступления ред. 2025 (§6.2)
    reg_editions = sorted(
        (m for m in docs_meta if m.get("kind") == "regulation"),
        key=lambda m: m.get("edition") or "")
    for i, reg in enumerate(reg_editions):
        if reg.get("status") == "revoked" and not reg.get("valid_to"):
            nxt = reg_editions[i + 1] if i + 1 < len(reg_editions) else None
            if nxt is not None:
                reg["valid_to"] = nxt.get("valid_from")

    # refs.json: doc_numbers (v1-совместимо) + doc_aliases (v2, exact-ref boost)
    refs = {
        "doc_numbers": [{"doc_id": m["doc_id"], "numbers": m["storage_numbers"]}
                        for m in docs_meta if m.get("storage_numbers")],
        "doc_aliases": build_aliases(docs_meta),
    }

    # BM25 по text_ctx (§9.1): собственный токенизатор, единый с запросом
    texts = [c["text_ctx"] for c in all_chunks]
    token_lists = [tokenize_ru(t) for t in texts]
    vocab = sorted({t for toks in token_lists for t in toks})
    vocab_map = {t: i for i, t in enumerate(vocab)}
    ids_list = [np.asarray([vocab_map[t] for t in toks], dtype=np.int32)
                for toks in token_lists]
    ids_flat = (np.concatenate(ids_list) if ids_list
                else np.zeros(0, dtype=np.int32))
    row_lengths = np.asarray([len(a) for a in ids_list], dtype=np.int32)

    gz_path = outdir / "chunks.jsonl.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    (outdir / "docs.json").write_text(
        json.dumps(docs_meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "refs.json").write_text(
        json.dumps(refs, ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez_compressed(outdir / "bm25.index.npz",
                        ids_flat=ids_flat,
                        row_lengths=row_lengths,
                        vocab=np.asarray(vocab, dtype=np.str_),
                        token_pattern=np.asarray([TOKENIZER_ID]))

    kb_hash = hashlib.sha256()
    for f_name, txt in sorted(documents):
        kb_hash.update(hashlib.sha256(f_name.encode("utf-8")).digest())
        kb_hash.update(hashlib.sha256(txt.encode("utf-8")).digest())
    manifest = {
        "kb_sha256": kb_hash.hexdigest(),
        "mode": "bm25_v2",
        "tokenizer": TOKENIZER_ID,
        "emb_model": None,
        "emb_dim": None,
        "n_chunks": len(all_chunks),
        "schema_version": 3,
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "build_s": round(time.time() - t0, 2),
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    compromised = sum(1 for notes in notes_by_doc.values()
                      for n in notes if n.startswith("compromised"))
    print(f"Индекс собран: {len(all_chunks)} чанков из {len(documents)} файлов; "
          f"compromised-строк: {compromised}; {manifest['build_s']} с", file=sys.stderr)
    return len(all_chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка индекса v2 (BM25 по пунктам)")
    parser.add_argument("--build-index", action="store_true")
    parser.add_argument("--kb", default=None, help="путь до kb.zip (иначе поиск)")
    parser.add_argument("--outdir", default="index")
    args = parser.parse_args()
    if not args.build_index:
        parser.error("передай --build-index")
    kb = Path(args.kb) if args.kb else find_kb_zip()
    if kb is None:
        sys.exit("kb.zip не найден: передай --kb путь до архива базы")
    count = build_index(kb, Path(args.outdir))
    print("Готово, чанков:", count)


if __name__ == "__main__":
    main()
