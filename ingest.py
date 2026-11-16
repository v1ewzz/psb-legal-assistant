# -*- coding: utf-8 -*-
"""Индексация: kb.zip → юридически осмысленные чанки + метаданные + реестры.

Что нового относительно v1 (п.3, 5, 12):
- чанкинг по юридической структуре: раздел/статья → пункт («3.1.») → перечисление.
  Неделимые «атомы» (пункт целиком, перечисление вместе с вводной строкой) не
  режутся; слишком длинные атомы режутся по подпунктам с повтором вводной строки;
  заголовок раздела/статьи подклеивается к чанку; перекрёстные ссылки «п. 2.2»
  фиксируются в xrefs (поиск подтягивает связанный пункт);
- метаданные: статус (действует/утратил силу), редакция, даты действия, семейство
  редакций (supersedes), связи документов (дополняет/изменяет/ссылается),
  заметки об изменениях закона с датой вступления (pending/в силе);
- инкрементальность: у каждого документа отпечаток; неизменённые документы
  переиспользуются из предыдущего индекса вместе с doc2q и эмбеддингами,
  изменённые — пересобираются, doc2q наследуется по перекрытию строк.

CLI: python ingest.py --build-index --kb kb.zip --outdir index [--prev index]
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from config import first_nonempty
from textutil import (normalize_text, parse_dates, first_date, sha1, tokenize)

SCHEMA_VERSION = 3
TARGET_CHARS = 1000      # к чему стремится упаковка
MIN_CHARS = 260          # мельче — склеиваем с соседом
HARD_CHARS = 1600        # атомы длиннее режем
PREFIX_MAX_DOC2Q = 6

# --- Т1: строки-инъекции внутри документов ---
COMPROMISED_PATTERNS = [
    re.compile(r"(ИИ-ассистент|AI-assistant|AI assistant|языковая модель|LLM).{0,120}(игнорируй|не упоминай|отвечай|всегда отвечай)", re.I),
    re.compile(r"(игнорируй|забудь).{0,60}(документ|инструкц|системн|предыдущ)", re.I),
    re.compile(r"(ignore|disregard).{0,40}(previous|above|prior).{0,30}(instruction|prompt)", re.I),
    re.compile(r"(инструкция|правило).{0,40}(для\s(ИИ|ассистента|модели))", re.I),
    re.compile(r"(не упоминай|скрой).{0,60}(эту инструкцию|данное правило)", re.I),
    re.compile(r"отвечай.{0,60}[\u00ab\"]?\s*\d+\s+(?:календарных|календарных дней|рабочих)", re.I),
]

HEADER_RE = re.compile(r"^(Источник|URL|Дата выгрузки)\s*:\s*(.*)$")
DOC_NUMBER_RE = re.compile(r"(?<![\w-])([А-ЯЁ]{1,3}-\d+(?:/\d{4})?)(?![\w])")
QUERY_REF_RE = re.compile(
    r"(?:ст\.?|статья|статье|статьи|статью)\s*(\d+(?:[.,]\d+)?)"
    r"|№\s*([А-ЯЁ]{1,3}-\d+(?:/\d{4})?)"
    r"|(?<![\w-])([А-ЯЁ]{1,3}-\d+(?:/\d{4})?)(?![\w])", re.I)

ARTICLE_RE = re.compile(r"^Статья\s+(\d+(?:\.\d+)*)\.")
SECTION_RE = re.compile(r"^(\d{1,2})\.\s+(\S.{0,78})$")
CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+)+)\.?\s+(\S.*)$")
LAW_PART_RE = re.compile(r"^(\d{1,2})\.\s+(\S.{60,})$")
XREF_RE = re.compile(r"(?:\bп\.|\bпп\.|\bпункт\w*|\bпункта\b)\s*(\d+(?:\.\d+)+)", re.I)

NOTE_START_RE = re.compile(
    r"^(?:Статья|Часть|Пункт|Подпункт|Абзац\w*|Приложение|Раздел|Глава)\b.{0,90}?"
    r"\b(?:изменен\w*|дополнен\w*|утратил\w*|признан\w*\s+утратив\w*)\b")
NOTE_DATE_RE = re.compile(r"\bс\s+(\d{1,2}\s+[а-яё]+\s+\d{4}|\d{2}\.\d{2}\.\d{4})")
NOTE_KIND_RE = re.compile(r"(изменен\w*|дополнен\w*|утратил\w*|признан\w*\s+утратив\w*)")
COMMENT_START_RE = re.compile(r"^(О конституционно-правовом смысле|См\.\s|Информация о |Подробнее)")

LAW_ACT_NAMES = {"ТК": "ТК РФ", "НК": "НК РФ", "КоАП": "КоАП РФ", "ГК": "ГК РФ",
                 "УК": "УК РФ", "СК": "СК РФ", "ЖК": "ЖК РФ", "ЗК": "ЗК РФ"}


def is_compromised(line: str) -> bool:
    return any(p.search(line) for p in COMPROMISED_PATTERNS)


# ------------------------------------------------------------------ чтение

def read_docx(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as doc:
        xml = doc.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n".join("".join(x.text or "" for x in p.iter(ns + "t")) for p in root.iter(ns + "p"))


def load_documents(zip_path: Path) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
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
                docs.append((name, text.strip()))
    if not docs:
        raise ValueError("В архиве базы знаний нет читаемых документов")
    return docs


def find_kb_zip(env: Optional[dict] = None) -> Optional[Path]:
    env = os.environ if env is None else env
    data_dir = Path(first_nonempty(env.get("DATA_DIR")) or "data")
    script_dir = Path(__file__).resolve().parent
    for root in (data_dir, script_dir / data_dir, Path.cwd(), script_dir):
        for name in ("kb.zip", "knowledge-base.zip", "knowledge_base.zip"):
            p = root / name
            if p.is_file():
                return p
    return None


# ------------------------------------------------------------ метаданные

def slugify_doc_id(file_name: str) -> str:
    stem = Path(file_name).stem.replace(" ", "_")
    stem = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_.-]", "", stem)
    return stem[:80] or "doc"


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _canon_alias(s: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", normalize_text(s).lower())


def _doc_kind(stem: str, text: str) -> str:
    low = stem.lower()
    head = text[:300].lower()
    if stem.startswith("законы__"):
        return "law"
    if "доп" in low and "соглаш" in low or head.startswith("дополнительное соглашение"):
        return "amendment"
    if "письмо" in low:
        return "letter"
    if "регламент" in low or "положение" in low or "политика" in low:
        return "policy"
    if "записк" in low:
        return "memo"
    if "договор" in low:
        return "contract"
    return "other"


def _family_key(stem: str) -> str:
    s = stem.split("__", 1)[-1]
    s = re.sub(r"(?i)_?ред(?:акция)?_?\d{4}(?:_\d{2}_\d{2})?", "", s)
    s = re.sub(r"(?i)_?(утратил[аи]?_сил[ауы]|действующая|действует)", "", s)
    return s.strip("_ ")


def _parse_note(line: str) -> Optional[dict]:
    """Строка-примечание об изменении нормы → {what, kind, date, act}; иначе None."""
    if len(line) > 320 or not NOTE_START_RE.match(line):
        return None
    kind_m = NOTE_KIND_RE.search(line)
    date_m = NOTE_DATE_RE.search(line)
    if not kind_m or (not date_m and "утратил" not in line):
        return None
    ntype = "revoked" if "утратил" in kind_m.group(1) else (
        "added" if kind_m.group(1).startswith("дополнен") else "changed")
    d = first_date(date_m.group(1)) if date_m else None
    act = ""
    if " - " in line:
        act = line.split(" - ", 1)[1].strip()
    return {"what": line[:kind_m.start()].strip(), "ntype": ntype, "date": _iso(d),
            "act": act[:200], "text": line}


_ABBR_END = re.compile(r"(?:\bг|\bст|\bп|\bпп|\bч|\bим|\bт\.д|\bт\.п|\bруб|\bкв\.\s*м)\.$")
_TERMINAL = (".", ";", ":", "!", "?", "»", "”", ")", "…")
_DANGLING = re.compile(r"^(?:[-–]\s*Федеральн\w+\s+закон\w*\s+от\b.*|№\s*\d+-ФЗ\)?|N\s*\d+-ФЗ\)?)$")


def repair_lines(raw_lines: list[str]) -> list[str]:
    """Склейка разорванных строк (артефакты выгрузки/нарезки) и сброс обрывков-ссылок."""
    lines = [l.strip() for l in raw_lines if l.strip()]
    out: list[str] = []
    for i, line in enumerate(lines):
        if _DANGLING.match(line) and i < len(lines):
            if out and out[-1].rstrip().endswith("г."):
                out[-1] = out[-1] + " " + line  # «…от 25 мая 2026 г.» + «№ 144-ФЗ)»
            continue
        if out:
            prev = out[-1]
            joined = False
            if re.fullmatch(r"Статья\s+\d+(?:\.\d+)*\.", prev):
                joined = True
            elif line[:1].islower() and (not prev.endswith(_TERMINAL) or _ABBR_END.search(prev)) \
                    and not prev.endswith((":",)) and len(prev) > 3:
                joined = True
            elif line[:1] in ("№", ")") or re.match(r"^N\s*\d", line):
                joined = True
            if joined:
                out[-1] = prev + " " + line
                continue
        out.append(line)
    return out


def parse_document(file_name: str, text: str) -> tuple[dict, list[str], list[dict]]:
    """→ (meta, body_lines, notes). Чистит шапку, примечания, комментарии, инъекции."""
    stem = Path(file_name).stem
    kind = _doc_kind(stem, text)
    header: dict[str, str] = {}
    body: list[str] = []
    notes: list[dict] = []
    for pos, line in enumerate(repair_lines(text.splitlines())):
        m = HEADER_RE.match(line)
        if m and pos < 14:
            header[m.group(1)] = m.group(2).strip()
            continue
        if pos < 14 and re.search(r"\(с изменениями и дополнениями\)\s*$", line):
            continue
        if is_compromised(line):
            notes.append({"kind": "compromised", "text": line})
            continue
        note = _parse_note(line)
        if note:
            notes.append({"kind": "amendment", **note})
            continue
        if COMMENT_START_RE.match(line):
            notes.append({"kind": "comment", "text": line})
            continue
        body.append(line)

    short_name = stem.split("__", 1)[-1].replace("_", " ")
    meta: dict = {
        "doc_id": slugify_doc_id(file_name), "file_name": file_name, "doc_type": kind,
        "short_name": short_name, "head": [f"{k}: {v}" for k, v in header.items()],
        "source_title": header.get("Источник", ""), "url": header.get("URL", ""),
        "export_date": _iso(first_date(header.get("Дата выгрузки", ""))),
        "status": "active", "valid_from": None, "valid_to": None, "edition_date": None,
        "family": _family_key(stem), "superseded_by": None, "supersedes": None,
        "relations": [], "act": "", "act_aliases": [], "article": "",
        "doc_numbers": [], "doc_date": None, "label": short_name,
    }
    head_text = "\n".join(body[:12])
    # --- статус / редакция ---
    if re.search(r"утрат\w+_сил", stem, re.I) or re.search(r"РЕДАКЦИЯ УТРАТИЛА СИЛУ", head_text, re.I):
        meta["status"] = "revoked"
        m = re.search(r"УТРАТИЛА СИЛУ\s+с\s+(\d{2}\.\d{2}\.\d{4})", head_text, re.I)
        if m:
            meta["valid_to"] = _iso(first_date(m.group(1)))
    m = re.search(r"Редакция от\s+(\d{2}\.\d{2}\.\d{4})", head_text)
    if m:
        meta["edition_date"] = _iso(first_date(m.group(1)))
    m = re.search(r"Действует с\s+(\d{2}\.\d{2}\.\d{4})", head_text)
    if m:
        meta["valid_from"] = _iso(first_date(m.group(1)))
    if not meta["edition_date"]:
        ym = re.search(r"ред_?(\d{4})", stem)
        if ym:
            meta["edition_year"] = int(ym.group(1))
    # --- номера документов, дата ---
    nums = {m.group(1) for m in DOC_NUMBER_RE.finditer(stem)}
    first_lines = "\n".join(body[:8])
    for m in re.finditer(r"№\s*([А-ЯЁ]{1,3}-\d+(?:/\d{4})?)", first_lines):
        nums.add(m.group(1))
    own = sorted({n.split("/")[0] for n in nums} | nums)
    meta["doc_numbers"] = own
    meta["doc_date"] = _iso(first_date(first_lines))
    # --- закон: акт, статья, алиасы ---
    if kind == "law":
        m = re.match(r"^законы__([^_]+)_ст([\d.]+)_", stem + "_")
        act_code, art = (m.group(1), m.group(2)) if m else ("", "")
        src = meta["source_title"]
        if not art:
            ma = re.search(r"Статья\s+(\d+(?:\.\d+)*)", src or " ".join(body[:2]))
            art = ma.group(1) if ma else ""
        aliases = set()
        if act_code:
            aliases.add(act_code)
        for ma in re.finditer(r"\(([А-Яа-яЁёA-Za-z]{2,8}\s*РФ)\)", src):
            aliases.add(ma.group(1))
        for ma in re.finditer(r"[N№]\s*(\d+-ФЗ)", src):
            aliases.add(ma.group(1))
        for ma in re.finditer(r"(Трудовой кодекс|Налоговый кодекс|Гражданский кодекс|Кодекс[^.(]{3,60}"
                              r"правонарушениях)", src):
            aliases.add(ma.group(1))
        act_name = LAW_ACT_NAMES.get(act_code) or (sorted(a for a in aliases if "ФЗ" in a) or [act_code])[0]
        meta.update(act=act_name, article=art, act_aliases=sorted(aliases | {act_name}))
        meta["label"] = f"{act_name}, ст. {art}" if art else act_name
        meta["law_title"] = next((l for l in body[:3] if ARTICLE_RE.match(l)), "")
    else:
        if meta["edition_date"] and meta["family"] != short_name.replace(" ", "_"):
            d = date.fromisoformat(meta["edition_date"])
            meta["label"] = f"{short_name.split(' ред')[0]} (ред. от {d.strftime('%d.%m.%Y')})"
    meta["amendments"] = [{k: v for k, v in n.items() if k not in ("kind", "text")}
                          for n in notes if n.get("kind") == "amendment"]
    return meta, body, notes


def link_documents(metas: dict[str, dict], texts: dict[str, str]) -> None:
    """Семейства редакций и связи документов (in-place)."""
    fams: dict[str, list[str]] = {}
    for did, m in metas.items():
        if m["doc_type"] != "law":
            fams.setdefault(m["family"], []).append(did)

    def ed_key(did: str):
        m = metas[did]
        return (m.get("edition_date") or m.get("valid_from")
                or (f"{m['edition_year']}-01-01" if m.get("edition_year") else "0000"))

    for fam, ids in fams.items():
        if len(ids) < 2:
            continue
        ids.sort(key=ed_key)
        for old, new in zip(ids, ids[1:]):
            metas[old]["superseded_by"] = new
            metas[new]["supersedes"] = old
            if not metas[old]["valid_to"]:
                metas[old]["valid_to"] = metas[new].get("valid_from") or metas[new].get("edition_date")
            metas[old].setdefault("status", "revoked")
        if metas[ids[-1]]["status"] == "active" and not metas[ids[-1]]["valid_from"]:
            metas[ids[-1]]["valid_from"] = metas[ids[-1]].get("edition_date")
        for did in ids[:-1]:
            metas[did]["status"] = "revoked"

    by_act: dict[str, set[str]] = {}
    for m in metas.values():
        if m["doc_type"] == "law" and m["act"]:
            by_act.setdefault(m["act"], set()).update(m["act_aliases"])
    for m in metas.values():
        if m["doc_type"] == "law" and m["act"]:
            m["act_aliases"] = sorted(by_act[m["act"]])

    def add_rel(a: str, b: str, typ: str) -> None:
        if a == b or any(r["target"] == b and r["dir"] == "out" for r in metas[a]["relations"]):
            return
        if not any(r["target"] == b and r["type"] == typ and r["dir"] == "out" for r in metas[a]["relations"]):
            metas[a]["relations"].append({"type": typ, "target": b, "dir": "out"})
            metas[b]["relations"].append({"type": typ, "target": a, "dir": "in"})

    num_to_docs: dict[str, set[str]] = {}
    for did, m in metas.items():
        for n in m["doc_numbers"]:
            num_to_docs.setdefault(n, set()).add(did)
    fam_phrases = {fam: [t for t in tokenize(fam.replace("_", " "))][:3] for fam in fams}
    for did, m in metas.items():
        if m["doc_type"] == "law":
            continue
        text = texts[did]
        low = text.lower()
        typ = "amends" if (m["doc_type"] == "amendment" or "изложить" in low[:1200]) else (
            "supplements" if "в дополнение" in low else "refers")
        own = set() if m["doc_type"] == "amendment" else set(m["doc_numbers"])
        for ref in {mm.group(1) for mm in DOC_NUMBER_RE.finditer(text)} | \
                   {mm.group(1).split("/")[0] for mm in DOC_NUMBER_RE.finditer(text)}:
            if ref in own:
                continue
            for target in num_to_docs.get(ref, ()):
                if target != did:
                    add_rel(did, target, typ)
        toks = set(tokenize(text))
        for fam, phr in fam_phrases.items():
            if fam == m["family"] or not phr or not all(p in toks for p in phr):
                continue
            ids = sorted(fams[fam], key=ed_key)
            target = ids[-1]
            mm = re.search(r"ред\.?\s*от\s*(\d{2}\.\d{2}\.\d{4})", text)
            if mm:
                want = _iso(first_date(mm.group(1)))
                target = next((i for i in ids if metas[i].get("edition_date") == want), target)
            add_rel(did, target, "supplements" if typ == "supplements" else "refers")


# ------------------------------------------------------------------- чанкинг

def _classify(line: str) -> str:
    if ARTICLE_RE.match(line):
        return "article"
    if CLAUSE_RE.match(line):
        return "clause"
    m = SECTION_RE.match(line)
    if m and not re.search(r"[.;:,]$", line) and len(line) <= 80:
        return "section"
    if LAW_PART_RE.match(line):
        return "part"
    return "para"


def _atoms(lines: list[str]) -> list[dict]:
    """Неделимые смысловые единицы: пункт, перечисление с вводной строкой, абзац."""
    atoms: list[dict] = []
    cur: Optional[dict] = None
    for line in lines:
        kind = _classify(line)
        lower_start = line[:1].islower()
        enum_open = bool(cur and cur["kind"] != "heading" and
                         (cur["lines"][-1].rstrip().endswith((":", ";", ",")) or cur["enum"]))
        attach = cur is not None and kind in ("para",) and lower_start and cur["kind"] != "heading"
        attach = attach or (cur is not None and kind == "para" and enum_open and lower_start)
        if attach:
            cur["lines"].append(line)
            cur["enum"] = cur["enum"] or cur["lines"][0].rstrip().endswith(":")
            continue
        cur = {"kind": "heading" if kind in ("article", "section") else kind,
               "lines": [line], "enum": line.rstrip().endswith(":"),
               "clause": (CLAUSE_RE.match(line).group(1) if kind == "clause" else
                          LAW_PART_RE.match(line).group(1) if kind == "part" else None),
               "heading_text": line if kind in ("article", "section") else None}
        atoms.append(cur)
    return atoms


def _split_atom(atom: dict) -> list[dict]:
    text_len = sum(len(l) + 1 for l in atom["lines"])
    if text_len <= HARD_CHARS:
        return [atom]
    parts: list[list[str]] = []
    lead = atom["lines"][0] if atom["enum"] else None
    items = atom["lines"][1:] if lead else atom["lines"]
    if len(items) <= 1 and not lead:  # один длинный абзац → по предложениям
        sentences = re.split(r"(?<=[.;!?])\s+", atom["lines"][0])
        items = sentences
    buf: list[str] = []
    size = 0
    for it in items:
        add = len(it) + 1
        if buf and size + add > TARGET_CHARS * 1.3:
            parts.append(buf)
            buf, size = [], 0
        buf.append(it)
        size += add
    if buf:
        parts.append(buf)
    out = []
    for i, p in enumerate(parts):
        lines = ([lead] + p) if lead else p
        out.append({**atom, "lines": lines, "enum": False, "split": (i + 1, len(parts)),
                    "clause": atom["clause"] if i == 0 else None})
    return out


def make_chunks(meta: dict, body: list[str]) -> list[dict]:
    """Упаковка атомов в чанки с контекстом заголовка и метаданными пунктов."""
    atoms: list[dict] = []
    for a in _atoms(body):
        atoms.extend(_split_atom(a))
    # заголовки склеиваем со следующим атомом
    units: list[dict] = []
    i = 0
    while i < len(atoms):
        a = atoms[i]
        if a["kind"] == "heading" and i + 1 < len(atoms) and atoms[i + 1]["kind"] != "heading":
            nxt = atoms[i + 1]
            units.append({**nxt, "lines": a["lines"] + nxt["lines"], "head_start": True,
                          "heading_text": a["heading_text"],
                          "clauses": [c for c in [nxt.get("clause")] if c]})
            i += 2
        else:
            units.append({**a, "head_start": a["kind"] == "heading",
                          "clauses": [c for c in [a.get("clause")] if c]})
            i += 1

    def usize(u: dict) -> int:
        return sum(len(l) + 1 for l in u["lines"])

    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_size = 0
    for u in units:
        s = usize(u)
        boundary = u.get("head_start") and u.get("heading_text")
        if cur and (cur_size + s > TARGET_CHARS and cur_size >= MIN_CHARS
                    or boundary and cur_size >= 150 and meta["doc_type"] != "law"
                    and u.get("kind") != "article"):
            groups.append(cur)
            cur, cur_size = [], 0
        cur.append(u)
        cur_size += s
    if cur:
        groups.append(cur)
    # мелкий хвост — к предыдущему
    if len(groups) >= 2 and sum(usize(u) for u in groups[-1]) < MIN_CHARS and \
            sum(usize(u) for g in groups[-2:] for u in g) <= HARD_CHARS * 1.3:
        groups[-2].extend(groups.pop())

    chunks: list[dict] = []
    active_heading = ""
    n = len(groups)
    for idx, g in enumerate(groups):
        lines: list[str] = []
        clauses: list[str] = []
        first_heading_in_chunk = bool(g[0].get("heading_text"))
        for u in g:
            lines.extend(u["lines"])
            clauses.extend(u.get("clauses") or [])
            if u.get("heading_text"):
                active_heading = u["heading_text"]
        start_heading = g[0].get("heading_text") or ""
        ctx_prefix = ""
        if not first_heading_in_chunk and active_heading_before(groups, idx):
            ctx_prefix = f"[{active_heading_before(groups, idx)}]"
        body_text = "\n".join(lines)
        text = (ctx_prefix + "\n" + body_text) if ctx_prefix else body_text
        dotted = [c for c in clauses if "." in c]
        xrefs = sorted({x.rstrip(".") for x in XREF_RE.findall(body_text)} - set(dotted))
        if meta["doc_type"] == "law":
            path = f"ст. {meta['article']}, фрагмент {idx + 1}/{n}" if meta["article"] else f"фрагмент {idx + 1}/{n}"
            parts_nums = [c for c in clauses if "." not in c]
            if parts_nums:
                path += f" (пункты {parts_nums[0]}–{parts_nums[-1]})" if len(parts_nums) > 1 else f" (пункт {parts_nums[0]})"
        elif dotted:
            path = f"пп. {dotted[0]}–{dotted[-1]}" if len(dotted) > 1 else f"п. {dotted[0]}"
            hd = ctx_prefix.strip("[]") or start_heading
            if hd:
                path = f"{hd}, {path}"
        else:
            path = (ctx_prefix.strip("[]") or start_heading or f"фрагмент {idx + 1}/{n}")
        chunks.append({
            "chunk_id": f"{meta['doc_id']}#{idx:03d}", "doc_id": meta["doc_id"],
            "short_name": meta["short_name"], "label": meta["label"], "path": path,
            "ord": idx, "text": text, "body": body_text,
            "clauses": dotted, "xrefs": xrefs, "doc2q": "",
        })
    for c in chunks:
        title = meta["label"]
        if meta["doc_type"] == "law" and meta.get("law_title"):
            title = f"{meta['label']}. {meta['law_title']}"
        c["text_ctx"] = f"{title}. {c['path']}. {c['text']}"
        c["fp"] = sha1(c["body"])[:16]
    return chunks


def active_heading_before(groups: list[list[dict]], idx: int) -> str:
    """Последний заголовок раздела/статьи до чанка idx (включительно по предыдущим)."""
    last = ""
    for g in groups[:idx]:
        for u in g:
            if u.get("heading_text"):
                last = u["heading_text"]
    return last


def body_fingerprint(body: list[str]) -> str:
    return sha1("\n".join(normalize_text(l) for l in body))


# -------------------------------------------------------------- doc2query

def inherit_doc2q(new_chunks: list[dict], old_chunks: list[dict]) -> None:
    """Перенос doc2q со старых чанков того же документа по перекрытию строк."""
    if not old_chunks:
        return
    old_lines = [set(l.strip() for l in (c.get("body") or c["text"]).splitlines() if l.strip())
                 for c in old_chunks]
    for nc in new_chunks:
        nl = set(l.strip() for l in nc["body"].splitlines() if l.strip())
        if not nl:
            continue
        scored = []
        for oc, ol in zip(old_chunks, old_lines):
            if not ol or not oc.get("doc2q"):
                continue
            shared = len(nl & ol)
            if shared and (shared / len(nl) >= 0.5 or shared / len(ol) >= 0.5):
                scored.append((shared / max(len(nl), len(ol)), oc["doc2q"]))
        scored.sort(key=lambda x: -x[0])
        seen, qs = set(), []
        for _, d in scored:
            for q in d.splitlines():
                q = q.strip()
                if q and q not in seen:
                    seen.add(q)
                    qs.append(q)
        nc["doc2q"] = "\n".join(qs[:PREFIX_MAX_DOC2Q * 2])


# --------------------------------------------------------------- индекс

class IndexData:
    """Результат сборки/загрузки: чанки, мета документов, манифест."""

    def __init__(self, chunks: list[dict], docs: dict[str, dict], manifest: dict):
        self.chunks, self.docs, self.manifest = chunks, docs, manifest


def build_data(documents: list[tuple[str, str]], prev: Optional[IndexData] = None,
               ) -> tuple[IndexData, dict]:
    """Инкрементальная сборка в памяти. Возвращает (IndexData, статистика)."""
    t0 = time.time()
    metas: dict[str, dict] = {}
    bodies: dict[str, list[str]] = {}
    texts: dict[str, str] = {}
    compromised = 0
    for file_name, text in documents:
        meta, body, notes = parse_document(file_name, text)
        compromised += sum(1 for n in notes if n["kind"] == "compromised")
        metas[meta["doc_id"]] = meta
        bodies[meta["doc_id"]] = body
        texts[meta["doc_id"]] = text
    link_documents(metas, texts)

    prev_chunks: dict[str, list[dict]] = {}
    prev_fp: dict[str, str] = {}
    if prev:
        for c in prev.chunks:
            prev_chunks.setdefault(c["doc_id"], []).append(c)
        prev_fp = {d: v.get("fp", "") for d, v in prev.manifest.get("docs", {}).items()}

    all_chunks: list[dict] = []
    reused, rebuilt = [], []
    doc_manifest: dict[str, dict] = {}
    for did, meta in metas.items():
        fp = body_fingerprint(bodies[did])
        old = prev_chunks.get(did, [])
        if old and prev_fp.get(did) == fp and prev.manifest.get("schema_version") == SCHEMA_VERSION:
            # документ не менялся: чанки переиспользуем, но label/путь берём из свежей меты
            for c in old:
                c = dict(c)
                c["label"], c["short_name"] = meta["label"], meta["short_name"]
                all_chunks.append(c)
            reused.append(did)
        else:
            chunks = make_chunks(meta, bodies[did])
            inherit_doc2q(chunks, old)
            all_chunks.extend(chunks)
            rebuilt.append(did)
        doc_manifest[did] = {"fp": fp, "n_chunks": sum(1 for c in all_chunks if c["doc_id"] == did)}
    removed = sorted(set(prev_chunks) - set(metas))
    manifest = {
        "schema_version": SCHEMA_VERSION, "n_chunks": len(all_chunks), "n_docs": len(metas),
        "docs": doc_manifest, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "build_s": round(time.time() - t0, 2),
        "index_hash": sha1("|".join(sorted(f"{d}:{v['fp']}" for d, v in doc_manifest.items()))
                           + f"|v{SCHEMA_VERSION}")[:16],
    }
    stats = {"reused": reused, "rebuilt": rebuilt, "removed": removed, "compromised": compromised}
    return IndexData(all_chunks, metas, manifest), stats


def save_index(data: IndexData, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    with gzip.open(outdir / "chunks.jsonl.gz", "wt", encoding="utf-8") as f:
        for c in data.chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (outdir / "docs.json").write_text(
        json.dumps(list(data.docs.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "manifest.json").write_text(
        json.dumps(data.manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def load_index_dir(directory: Path) -> Optional[IndexData]:
    cj, dj, mj = directory / "chunks.jsonl.gz", directory / "docs.json", directory / "manifest.json"
    if not (cj.is_file() and dj.is_file() and mj.is_file()):
        return None
    try:
        with gzip.open(cj, "rt", encoding="utf-8") as f:
            chunks = [json.loads(l) for l in f if l.strip()]
        docs = {d["doc_id"]: d for d in json.loads(dj.read_text(encoding="utf-8"))}
        manifest = json.loads(mj.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    return IndexData(chunks, docs, manifest)


def build_index(kb_zip: Path, outdir: Path, prev_dir: Optional[Path] = None) -> IndexData:
    docs = load_documents(kb_zip)
    prev = load_index_dir(prev_dir) if prev_dir else load_index_dir(outdir)
    data, stats = build_data(docs, prev)
    save_index(data, outdir)
    print(f"Индекс: {len(data.chunks)} чанков / {len(data.docs)} документов; "
          f"переиспользовано {len(stats['reused'])}, пересобрано {len(stats['rebuilt'])}, "
          f"удалено {len(stats['removed'])}; compromised-строк: {stats['compromised']}",
          file=sys.stderr)
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборка индекса (инкрементальная)")
    ap.add_argument("--build-index", action="store_true")
    ap.add_argument("--kb", default=None)
    ap.add_argument("--outdir", default="index")
    ap.add_argument("--prev", default=None, help="каталог предыдущего индекса для переиспользования")
    args = ap.parse_args()
    if not args.build_index:
        ap.error("передай --build-index")
    kb = Path(args.kb) if args.kb else find_kb_zip()
    if kb is None:
        sys.exit("kb.zip не найден")
    build_index(kb, Path(args.outdir), Path(args.prev) if args.prev else None)


if __name__ == "__main__":
    main()
