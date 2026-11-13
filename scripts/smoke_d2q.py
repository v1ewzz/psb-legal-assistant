# -*- coding: utf-8 -*-
"""Смок retrieval без LLM: докрутил ли doc2query лексические пропасти."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import from_env
from retrieval import Retriever, find_artifact, load_chunks

cfg = from_env()
chunks = load_chunks(cfg)
retr = Retriever(chunks, find_artifact(cfg, "bm25.index.npz"),
                 json.loads(find_artifact(cfg, "refs.json").read_text(encoding="utf-8")),
                 sibling_fill=True)
print("чанков:", len(chunks))

PROBES = [
    ("Q7 ночной проезд",
     "К нашему складу в Химках грузовик приезжает иногда в два часа ночи. "
     "По договору с Северным Складом нас должны пропускать?", "4.3"),
    ("Q12 правила на испытательном",
     "Сотрудник работает на испытательном сроке. На него распространяются "
     "обычные трудовые правила?", "70"),
    ("Q5 одобрение 12 млн",
     "Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было "
     "одобрение генерального директора?", "2.4"),
    ("Q18 передача оригинала",
     "Подписали новый договор, оригинал пока у меня. Кому передавать дальше?", "3.2"),
]
for label, q, want in PROBES:
    sel = retr.search(q, top_k=4)
    paths = [f"{c['path']}|{c['doc_id'][-16:]}" for c in sel]
    hit = any(want in c["path"] or want in c["text"][:80] for c in sel)
    print(f"{label}: {'HIT ' if hit else 'MISS'} {paths}")
