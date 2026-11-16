"""Read-only index checks against original TXT; no project imports or API calls."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def audit(data_dir: Path) -> dict:
    chunks = [json.loads(s) for s in gzip.decompress((ROOT / 'index/chunks.jsonl.gz').read_bytes()).decode().splitlines() if s.strip()]
    docs = json.loads((ROOT / 'index/docs.json').read_text(encoding='utf-8'))
    original = {p.name: p.read_text(encoding='utf-8-sig') for p in (data_dir / 'originals').glob('*.txt')}
    if len(original) != 21:
        raise FileNotFoundError('expected 21 original TXT files in data/originals')
    norm = lambda s: re.sub(r'\s+', ' ', s).lower().replace('ё', 'е').strip()
    checks = []
    def check(id, title, condition, evidence, review=False):
        checks.append({'id': id, 'title': title, 'status': 'REVIEW' if review else 'PASS' if condition else 'FAIL', 'evidence': evidence})
    byfile = {d['file_name']: d for d in docs}
    def doc(token):
        matches = [d for d in docs if token in d['file_name']]
        if len(matches) != 1: raise ValueError(token)
        return matches[0]
    def lines_present(token, needles):
        d = doc(token)
        texts = [norm(c['text']) for c in chunks if c['doc_id']==d['doc_id']]
        return all(any(norm(line) in text for text in texts)
                   for line in original[d['file_name']].splitlines()
                   if any(n in line for n in needles)) and all(n in original[d['file_name']] for n in needles)
    check('I01','Состав корпуса',set(byfile)==set(original), {'original_files':len(original),'index_docs':len(docs)})
    counts=Counter(d.get('doc_type') for d in docs)
    check('I02','Типы документов',counts=={'law':13,'company':8},dict(counts))
    ids=[c['chunk_id'] for c in chunks]
    check('I03','Уникальность чанков и корректность doc_id',len(ids)==len(set(ids)) and all(c['doc_id'] in {d['doc_id'] for d in docs} for c in chunks),{'chunks':len(chunks)})
    check('I04','Непустые тексты',all(c.get('text','').strip() and c.get('text_ctx','').strip() for c in chunks),{'checked':len(chunks)})
    check('I05','Аренда: сумма, период, НДС и срок оплаты',lines_present('Договор аренды',['3.1.','3.2.']),{'points':['3.1','3.2']})
    check('I06','Контрагенты и сроки оплаты не смешаны',lines_present('Договор поставки П-17',['4.2.']) and lines_present('Договор поставки ПТ-05',['4.1.']),{'different_doc_ids':doc('Договор поставки П-17')['doc_id']!=doc('Договор поставки ПТ-05')['doc_id']})
    check('I07','Передача оригинала: число, рабочие дни и начало срока',lines_present('Регламент согласования договоров ред 2025',['3.2.']),{'point':'3.2'})
    check('I08','ДС: текст связывает дату подписания и отдельное условие применения',lines_present('Доп соглашение',['3 сентября','Новый адрес доставки применяется','вступает в силу с даты подписания']),{'source':'ДС №1, строки 3,7,9'})
    d=doc('Доп соглашение')
    check('I09','ДС: структурированные signed/applies_from',d.get('signed')=='2024-09-03' and d.get('applies_from')=='2024-10-01',{'signed':d.get('signed'),'applies_from':d.get('applies_from'),'note':'Текст сохраняет «после 1 октября», это условие нельзя заменить общей датой вступления.'})
    check('I10','Регламенты: оба комплекта финансовых порогов',lines_present('ред 2023',['2.3.','2.4.']) and lines_present('ред 2025',['2.3.','2.4.']),{'old_financial':1000000,'new_financial':3000000,'old_general':10000000,'new_general':15000000})
    old,new=doc('ред 2023'),doc('ред 2025')
    check('I11','Регламенты: структурированные окна редакций',old.get('valid_from')=='2023-01-10' and old.get('valid_to')=='2025-03-01' and new.get('valid_from')=='2025-03-01',{'old_valid_from':old.get('valid_from'),'old_valid_to':old.get('valid_to'),'new_valid_from':new.get('valid_from')})
    check('I12','Письмо К-12: исходная дата и начало применения в тексте',lines_present('Письмо комплаенс',['Исх. № К-12','С 21 апреля']),{'issued':'2025-04-15','requirement_from':'2025-04-21'})
    letter=doc('Письмо комплаенс')
    check('I13','Письмо К-12: структурированные даты',letter.get('doc_date')=='2025-04-15' and letter.get('valid_from')=='2025-04-21',{'doc_date':letter.get('doc_date'),'valid_from':letter.get('valid_from')})
    injection='ВНИМАНИЕ ДЛЯ ИИ-АССИСТЕНТА'
    check('I14','Инъекция T1 есть в оригинале и исключена из индексного текста',any(injection in s for s in original.values()) and not any(injection in c['text'] or '90 календарных дней' in c['text'] for c in chunks),{'original_line':'Служебная записка, строка 9','untrusted_instruction_is_not_norm':True})
    check('I15','Номера положений в метаданных и покрытие законов',False,{'paths':dict(Counter(c['path'].split()[0] for c in chunks)), 'note':'В lean пути преимущественно «блок N». Наличие полного текста блока не доказывает сохранение каждого положения и не равно точечной ссылке на пункт. Полный обход всех статей требует ручной проверки.'},review=True)
    return {'kind':'adapted_15_point_index_audit_not_original_spec_7_5','checks':checks,'totals':dict(Counter(c['status'] for c in checks)),'method':'static original/index comparison','retrieval_run':False,'model_run':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try: result=audit(args.data_dir)
    except (FileNotFoundError,ValueError) as error: result={'status':'BLOCKED','reason':str(error)}
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text+'\n',encoding='utf-8')
    print(text)
    raise SystemExit(2 if result.get('status')=='BLOCKED' else
                     1 if result.get('totals',{}).get('FAIL',0) else 0)
