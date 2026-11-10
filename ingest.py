# -*- coding: utf-8 -*-
"""Ingestion: парсеры 21 файла, чанкинг, docs/refs/edges, сборка индекса (§7).

CLI: python ingest.py --build-index → chunks.jsonl.gz, docs.json, refs.json,
edges.json, emb.npy, bm25.index.npz, manifest.json (в index/).
Паттерны амендментов A (в шапке) и B (inline); compromised-детектор на строках;
ДС: извлечение операций set_text с signed и applies_from (§7.3).
"""
# TODO(Фаза 1): реализовать + чеклист ручной проверки §7.5 (15 пунктов).
