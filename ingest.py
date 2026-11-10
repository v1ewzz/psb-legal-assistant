# -*- coding: utf-8 -*-
"""Ingestion v1 (Сабмит №1): kb.zip → чанки, реестры, BM25-индекс (§7 упрощённо).

Что делает:
- читает txt/md/docx из kb.zip (kb.zip/knowledge-base.zip/knowledge_base.zip);
- отрезает шапку документов (Источник/URL/Дата выгрузки), закрытия-амендменты
  (паттерны A/B §3.2) — в notes, не в чанк-текст;
- детектор compromised-строк (инъекция Т1, §10.2) — помечает, из retrieval
  такие чанки исключаются (guard.py делает полный вариант в Фазе 3);
- чанкинг абзацами: 200–900 символов пакета (§7.4 упрощённо);
- реестры: docs.json (документы), refs.json (номера реквизитов);
- BM25-индекс (bm25s) по text_ctx; артефакт — ОДИН плоский файл
  bm25.index.npz (ids_flat + row_lengths + vocab), чтобы уложиться в
  «файлы в корне ZIP, без вложенных папок» (§2.3).

Dense-канал (emb.npy) в v1 отсутствует — деградация по цепочке §8.3
это допустимый режим (bm25 + exact-ref); полная программа — Фаза 1 (§7–8).

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
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Iterable, Optional

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
    re.compile(r"отвечай.{0,60}[\u00ab\"]?\s*\d+\s+(?:календарных|календарных дней|рабочих)", re.I),
]# Амендменты (§3.2): паттерн A (в шапке) и B (inline)
AMEND_RE = re.compile(
    r"^(?:Статья|Часть|Пункт|Подпункт)\b[^.]*\b(?:измен[аены]?\b|дополнен\b)[^.]*\s-\s"
    r"(?:Федеральн\w+\sзаконом?\sот\b|Федеральный закон\b)",
)

# Числа-реквизиты для exact-ref: А-08/2024, П-17/2024, ПТ-05/2023, К-12, ЮО-7, 14-П
DOC_NUMBER_RE = re.compile(r"\b([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)\b")

# Паттерны номеров/статей в запросе: «ст. 59», «п. 3.1», «№ К-12», «А-08/2024»
QUERY_REF_RE = re.compile(
    r"(?:ст\.?|статья)\s*(\d+(?:[.,]\d+)?)|№\s*([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)|\b([А-ЯЁ]{1,2}-\d+(?:/\d{4})?)\b",
    re.I,
)

TARGET_CHUNK_MAX = 900
TARGET_CHUNK_MIN = 200


def normalize_text(text: str) -> str:
    """Нормализация для токенизации: ё→е, гомоглифы (латиница→кириллица, Т9 §9.1)."""
    table = str.maketrans({
        "ё": "е", "Ё": "е",
        "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
        "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У", "a": "а",
        "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    })
    return text.translate(table)


def is_compromised(line: str) -> bool:
    """Т1: строка-инъекция внутри документа не должна попасть в чанк-текст."""
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


def _make_chunks(doc_id: str, short_name: str, text: str) -> tuple[list[dict], list[dict]]:
    """Чанкинг абзацами (§7.4 упрощённо), подавые амендменты/шапку/инъекции."""
    kept_chunks, notes = [], []
    buf: list[str] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buf, buffer_len
        if not buf:
            return
        joined = "\n".join(buf).strip()
        buf, buffer_len = [], 0
        if not joined:
            return
        kept_chunks.append({
            "text": joined,
        })

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if is_compromised(stripped):  # ловушка Т1: инъекция не попадает в чанк
            notes.append({"compromised": True, "text": stripped})
            continue
        if AMEND_RE.match(stripped):  # паттерны A/B: амендменты — в notes
            notes.append({"kind": "amendment", "text": stripped})
            continue
        # новая смысловая часть: вставляем крупными пакетами абзацев
        if buffer_len >= TARGET_CHUNK_MAX:
            flush()
        buf.append(stripped)
        buffer_len += len(stripped) + 1
    flush()

    # пакеты короче минимальной длины тоже индексируем — они несут смысл
    chunks = []
    for chunknum, chunk in enumerate(kept_chunks, 1):
        body = chunk["text"]
        if len(body) > TARGET_CHUNK_MAX * 2:
            # очень длинный пакет режем по предложениям с сохранением lead-строки
            parts, piece_len = [], 0
            piece: list[str] = []
            for sentence in re.split(r"(?<=[.!?;])\s+", body):
                if piece_len and piece_len + len(sentence) > TARGET_CHUNK_MAX * 1.5:
                    parts.append("\n".join(piece))
                    piece, piece_len = [], 0
                piece.append(sentence)
                piece_len += len(sentence) + 1
            if piece:
                parts.append("\n".join(piece))
            raw_parts = parts
        else:
            raw_parts = [body]
        for partnum, part in enumerate(raw_parts):
            part = part.strip()
            if len(part) < 40:  # мусорные обрывки не индексируем
                continue
            chunks.append({
                "chunk_id": f"{doc_id}_c{chunknum:02d}{partnum:02d}",
                "doc_id": doc_id,
                "short_name": short_name,
                "path": f"блок {chunknum}{('.' + str(partnum)) if len(raw_parts) > 1 else ''}",
                "text": part,
                "text_ctx": f"{short_name}. {normalize_text(part)}",
            })
    return chunks, notes


def slugify_doc_id(file_name: str) -> str:
    stem = Path(file_name).stem
    stem = stem.replace(" ", "_")
    stem = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_.-]", "", stem)
    return stem[:80] or "doc"


def build_registry(file_name: str, text: str) -> dict:
    """docs.json-записи: тип по префиксу имени, шапка законов, рексвизиты Т-номера."""
    stem = Path(file_name).stem
    doc_type = "law" if stem.startswith("законы__") else "company"
    short_name = stem.split("__", 1)[-1]
    head_lines, body_lines = [], []
    for i, line in enumerate(text.splitlines()):
        if i < 4 and re.match(r"^(Источник|URL|Дата выгрузки):", line.strip()):
            head_lines.append(line.strip())
        body_lines.append(line)
    # реквизиты для exact-ref (§6.3 упрощённо): номера доков + номер статьи из имени
    numbers = sorted(set(m.group(1) for m in DOC_NUMBER_RE.finditer(stem)))
    article_m = re.search(r"ст(\d+(?:[.,]\d+)?)", short_name, re.I)
    if article_m:
        numbers.append(article_m.group(1).replace(",", "."))
    numbers += sorted({m.group(1) for m in DOC_NUMBER_RE.finditer("\n".join(head_lines))})
    return {
        "doc_id": slugify_doc_id(file_name),
        "file_name": file_name,
        "doc_type": doc_type,
        "short_name": short_name,
        "head": head_lines,
        "storage_numbers": sorted(set(numbers)),
    }


def build_index(kb_zip: Path, outdir: Path) -> int:
    """Собрать все артефакты v1 в outdir; вернуть число чанков."""
    t0 = time.time()
    outdir.mkdir(parents=True, exist_ok=True)
    documents = load_documents(kb_zip)

    all_chunks: list[dict] = []
    docs_meta: dict[str, dict] = {}
    notes_html: dict[str, list] = {}
    for file_name, text in documents:
        meta = build_registry(file_name, text)
        doc_id = meta["doc_id"]
        docs_meta[doc_id] = meta
        chunks, notes = _make_chunks(doc_id, meta["short_name"], text)
        notes_html[doc_id] = notes
        all_chunks.extend(chunks)

    # refs.json: doc_id → номера файлов/шапки (для exact-ref boost, §6.3 упрощ.)
    refs = {"doc_numbers": [{"doc_id": d, "numbers": meta["storage_numbers"]}
                            for d, meta in docs_meta.items() if meta["storage_numbers"]]}

    # BM25 по text_ctx (§9.1 упрощённо: норм-текст, bm25s-токены — без стемминга)
    texts = [c["text_ctx"] for c in all_chunks]
    tokenized = bm25s.tokenize(texts, lower=True, stopwords=[])  # return_ids=True
    ids_list = [np.asarray(idx, dtype=np.int32) for idx in tokenized.ids]
    ids_flat = np.concatenate(ids_list) if ids_list else np.zeros(0, dtype=np.int32)
    row_lengths = np.asarray([len(a) for a in ids_list], dtype=np.int32)
    vocab = list(tokenized.vocab)

    gz_path = outdir / "chunks.jsonl.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    (outdir / "docs.json").write_text(
        json.dumps(list(docs_meta.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "refs.json").write_text(
        json.dumps(refs, ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez_compressed(outdir / "bm25.index.npz",
                        ids_flat=ids_flat,
                        row_lengths=row_lengths,
                        vocab=np.asarray(vocab, dtype=np.str_),
                        token_pattern=np.asarray(["(?u)\\b\\w\\w+\\b"]))

    kb_hash = hashlib.sha256()
    for f_name, txt in sorted(documents):
        kb_hash.update(hashlib.sha256(f_name.encode("utf-8")).digest())
        kb_hash.update(hashlib.sha256(txt.encode("utf-8")).digest())
    manifest = {
        "kb_sha256": kb_hash.hexdigest(),
        "mode": "bm25_only",
        "emb_model": None,
        "emb_dim": None,
        "n_chunks": len(all_chunks),
        "schema_version": 2,
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "build_s": round(time.time() - t0, 2),
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    compromised = sum(1 for notes in notes_html.values()
                      for n in notes if n.get("compromised"))
    print(f"Индекс собран: {len(all_chunks)} чанков из {len(documents)} файлов; "
          f"compromised-строк: {compromised}; {manifest['build_s']} с", file=sys.stderr)
    return len(all_chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка индекса v1 (BM25-only)")
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
