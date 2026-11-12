# -*- coding: utf-8 -*-
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.fact_coverage import facts_of, fact_matches
from retrieval import find_artifact, load_chunks
import config

cfg = config.from_env({})
chunks = load_chunks(cfg)

# Q1 эталон
gold = "Арендная плата по договору аренды № А-08/2024 с ООО «Северный Склад» — 450 000 рублей в месяц, включая НДС (п. 3.1). Вносится ежемесячно, не позднее 5-го числа текущего месяца (п. 3.2). Коммунальные платежи и электроэнергия оплачиваются отдельно по счетам арендодателя (п. 3.3)."
for f in facts_of(gold):
    print(f"FACT key={f['key']!r} raw={f['raw']!r} unit={f['unit']!r} pat={f['pat'].pattern!r}")
    hits = [c for c in chunks if fact_matches(f, c['text'] + '\n' + c['text_ctx'])]
    a08 = [c for c in chunks if 'А-08' in c['doc_id'] and c['path'] == 'п. 3.1']
    for c in a08:
        t = c['text'][:150]
        print('   п.3.1 text:', t)
        print('   matches:', fact_matches(f, c['text']))
