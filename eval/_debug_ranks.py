# -*- coding: utf-8 -*-
import json
import csv
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
import guard
import pipeline
import temporal
from retrieval import Retriever, find_artifact, load_chunks

cfg = config.from_env({})
chunks = load_chunks(cfg)
retriever = Retriever(chunks, find_artifact(cfg, 'bm25.index.npz'),
                      json.loads(find_artifact(cfg, 'refs.json').read_text(encoding='utf-8')))
docs = json.loads(find_artifact(cfg, 'docs.json').read_text(encoding='utf-8'))
qs = {r['id']: r['question'] for r in csv.DictReader(open(ROOT / 'data/demo_questions.csv', encoding='utf-8-sig'))}

targets = {'1': ['3.3'], '4': ['7'], '6': ['5', '3.3']}
numcache = [pipeline._number_keys(c['text'] + ' ' + c['text_ctx']) for c in chunks]

for qid, qq in qs.items():
    if qid not in targets:
        continue
    d = guard.parse_dialog(qq)
    queries = guard.heuristic_queries(d)
    tt = pipeline._temporal_text(d, queries)
    fl = temporal.search_filters(tt, docs)
    sel = retriever.search(queries, top_k=24, rerank_query=guard.standalone_query(d), client=None,
                           top_n=100, drop_doc_ids=fl.get('drop_doc_ids'),
                           boost_doc_ids=fl.get('boost_doc_ids'), deadline=None)
    ids = [c['chunk_id'] for c in sel]
    print(f'--- Q{qid}: queries={queries}')
    for t in targets[qid]:
        for i, n in enumerate(numcache):
            if t in n and ('Северный' in chunks[i]['doc_id'] or 'Альфа' in chunks[i]['doc_id']):
                rank = ids.index(chunks[i]['chunk_id']) + 1 if chunks[i]['chunk_id'] in ids else None
                print(f'  fact {t!r} -> {chunks[i]["path"]} [{chunks[i]["short_name"][:40]}] rank={rank}')
