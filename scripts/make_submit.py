# -*- coding: utf-8 -*-
"""Сборка сабмит-ZIP (§2.3, §22.2): только корень архива, без вложенных папок,
≤128 МБ. В ZIP: solution.py + плоские модули + requirements.txt (pinned) +
7 артефактов индекса в корне (chunks.jsonl.gz, docs.json, refs.json,
edges.json, emb.npy, bm25.index.npz, manifest.json). Без секретов.
"""
# TODO(Фаза 2): реализовать; чеклист §22.2 прогонять перед каждым сабмитом.
