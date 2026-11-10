# ПСБ · Юридический ассистент

RAG-ассистент по закрытой базе нормативных документов (13 законов + 8 документов компании ООО «Меридиан», 21 TXT-файл) для Hack&Change 2026.

Ассистент:
1. находит положения, в том числе из нескольких источников;
2. отвечает со ссылками на конкретные пункты/части;
3. обоснованно отказывается, если данных нет, и **не** отказывается, если ответ в базе есть;
4. различает: нехватку данных, ошибку в постановке вопроса, дату/редакцию, противоправные просьбы, атаки на роль;
5. имеет веб-чат с просмотром документов и подтверждающих фрагментов.

## Архитектура

Конвейер одного вопроса (`pipeline.answer_one` — общее ядро для batch и web):

```
parse_dialog → Analyze (LLM) → Retrieve → Temporal → Reason (LLM)
             → Verify → Decide → Render
```

Дифференциаторы (стоп-лист, не вырезать):
- **D1** Temporal-слой: редакции с окнами valid_from/valid_to, допсоглашения с собственной датой применения
- **D2** Детектор инъекций в документах
- **D3** Ссылки формирует код + детерминированная верификация
- **D4** Три зоны ответа (full / partial / refusal) + правило «не отказываться, если есть supported»

## Где какая логика

| Модуль | Ответственность |
|---|---|
| `solution.py` | Пакетный вход платформы: чтение `questions.csv` (utf-8-sig), дедуп id, ThreadPoolExecutor, периодическая запись `submission.csv`, дедлайн 16 мин, `os._exit` |
| `pipeline.py` | `answer_one(question, ctx, deadline)` — общее ядро для batch и web |
| `config.py` | Резолв переменных окружения, режим деградации, константы |
| `llm_client.py` | `chat() / embed() / rerank() / probe_all()`, ретраи, тайм-ауты, repair JSON |
| `ingest.py` | Парсеры 21 файла, чанкинг, edges, `--build-index` (артефакты 7 шт.) |
| `retrieval.py` | Каналы exact-ref / BM25 / dense → RRF → rerank → расширение (parent, refs, edges) |
| `temporal.py` | Окна редакций, версии пунктов, статусы `edition_*`, даты/сроки считает код |
| `guard.py` | Парсер диалога, детектор compromised-чанков (T1), вызов Analyze |
| `reason.py` | Reason-вызов, evidence-mapping по подвопросам |
| `verify.py` | Детерминированные проверки (cNN, цитаты, числа/даты) + LLM-verify в серой зоне |
| `decide.py` | Confidence-фичи, пороги T_high/T_low, зоны ответа |
| `render.py` | Подстановка ссылок из метаданных, шаблоны эталонного стиля, длина ≤10 000, одна строка |
| `webapp.py` | FastAPI + SSE, тот же `pipeline.answer_one` |

## Запуск

> Раздел заполняется по мере готовности. Целевая команда: `docker compose up`.

```
# локальная разработка
python -m pip install -r requirements.txt
cp .env.example .env   # заполнить ключи (ключи только в .env, в репо не класть)

# пакетный режим (как на платформе)
python solution.py

# веб-чат
uvicorn webapp:app --reload
```

## Переменные окружения

См. `.env.example`. Ключевые: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `EMB_MODEL`, `RERANKER_MODEL`, `DATA_DIR`, `MAX_WORKERS`.

## Структура репозитория

```
repo/
├── solution.py, pipeline.py, config.py, llm_client.py, ingest.py,
│   retrieval.py, temporal.py, guard.py, reason.py, verify.py,
│   decide.py, render.py, webapp.py   # плоские модули в корне (требование сабмита §2.3)
├── webapp.py + web/                  # веб-интерфейс (FastAPI + SSE + фронт)
├── index/                            # артефакты индекса для сборки сабмита
├── eval/                             # demo_gold.jsonl, synth_*.jsonl, runner.py, traps.jsonl
├── scripts/                          # make_submit.py, mock_api.py
├── tests/                            # pytest: temporal, verify, render, dialog-парсер
├── docs/team/                        # распределение работ и личные задачи команды
├── README.md, Dockerfile, compose.yaml, .dockerignore, .env.example, requirements.txt
```

**Правило импортов:** только `from retrieval import ...` — плоские модули, никаких подпакетов в коде, попадающем в сабмит.

## Команда

| Файл | Участник | Роль |
|---|---|---|
| `docs/team/00_Обзор_команды.md` | все | общая картина, матрица «роль × фаза», интерфейсы |
| `docs/team/01_Никита_системный_аналитик.md` | Никита | домен, ловушки, gold-факты, датасеты, README, фактчекинг |
| `docs/team/02_Вова_frontend.md` | Вова | чат, источники, trace, таймлайн |
| `docs/team/03_Иван_UIUX.md` | Иван | дизайн-система, доверие к ответу, презентация |
| `docs/team/04_Юсеф_backend.md` | Юсеф | API-клиент, мок, устойчивость, webapp, Docker, сборка сабмита |
| `docs/team/05_Илья_AIML.md` | Илья | ядро RAG (§5–15), калибровка, лид фаз 1–4 |

## Известные ограничения

- Скрытая база поставляется платформой в `data/kb.zip` — в репозиторий не входит.
- Ключи API приходят через окружение; в ZIP-сабмит и в репозиторий не кладутся.
- «Сегодня» в temporal-логике = дата выгрузки базы (10.10.2026), не системное время.
