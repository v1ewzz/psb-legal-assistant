# -*- coding: utf-8 -*-
"""Генератор мок-данных фронта из артефактов индекса (§18.1 п.5).

Фронт по контракту `docs/api_contract.md` получает список документов и полные
тексты с бэкенда (`GET /api/docs`, `GET /api/docs/{doc_id}`). Пока эндпоинтов
нет (Фаза 2–4), те же данные кладём статикой в `web/src/mock/kb.json` —
форма ответа совпадает с контрактом, поэтому при переходе на SSE фронт не
меняется.

Запуск (из корня репозитория):
    py scripts/gen_web_mock.py

Читает: index/docs.json, index/chunks.jsonl.gz (schema_version 3).
Пишет:  web/src/mock/kb.json.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index"
OUT = ROOT / "web" / "src" / "mock" / "kb.json"

# Поля документа, которые фронт показывает в списке и в шапке просмотра.
DOC_FIELDS = (
    "doc_id", "file_name", "doc_type", "kind", "short_name", "number",
    "parties", "doc_date", "valid_from", "valid_to", "edition",
    "applies_from", "amends_doc", "storage_numbers", "status",
)

# Поля чанка: `path` — реквизит («п. 3.1», «ч. 1»), из него строятся хлебные
# крошки; `text` — тело фрагмента для подсветки по индексам.
CHUNK_FIELDS = ("chunk_id", "doc_id", "path", "lead_in", "text")


def load_chunks() -> list[dict]:
    result = []
    with gzip.open(INDEX / "chunks.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                chunk = json.loads(line)
                result.append({k: chunk.get(k) for k in CHUNK_FIELDS})
    return result


def main() -> None:
    docs = json.loads((INDEX / "docs.json").read_text(encoding="utf-8"))
    manifest = json.loads((INDEX / "manifest.json").read_text(encoding="utf-8"))
    chunks = load_chunks()

    by_doc: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk["doc_id"], []).append(chunk)

    payload = {
        "source": "index/docs.json + index/chunks.jsonl.gz",
        "schema_version": manifest.get("schema_version"),
        "built": manifest.get("built"),
        # «Сегодня» системы = дата выгрузки базы (§11.1), не системное время.
        "today": "2026-10-10",
        "docs": [
            {**{k: doc.get(k) for k in DOC_FIELDS},
             "chunks": by_doc.get(doc["doc_id"], [])}
            for doc in docs
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    raw = OUT.stat().st_size
    print(f"docs: {len(payload['docs'])}, chunks: {len(chunks)}, "
          f"{raw / 1024:.0f} КБ → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
