#!/usr/bin/env bash
# Ночной раннер: ждёт сброса дневного лимита API (30 руб/сутки, 429 в 22:30),
# затем дожимает судью §1.2 по 6 сабмитам и собирает REPORT.md.
set -u
cd /d/hackaton_PSB
PY=/d/hackaton_PSB/.venv/Scripts/python.exe
LOG=eval/history/overnight.log
echo "=== overnight: старт $(date) ===" >> "$LOG"

while true; do
  h=$(date +%H%M)
  if [ "$h" -ge 0005 ] && { [ "$h" -lt 0900 ] || [ "$h" -ge 2200 ]; }; then break; fi
  echo "ждём полночь: $(date +%H:%M)" >> "$LOG"
  sleep 300
done

probe() {
  "$PY" -X utf8 -c "
import os, sys
from pathlib import Path
for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
    line=line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); os.environ.setdefault(k.strip(), v.strip().strip('\"'))
sys.path.insert(0, '.')
import config as C
from llm_client import LlmClient, ApiError
cfg = C.from_env()
c = LlmClient(cfg)
try:
    r = c.chat([{'role':'user','content':'ping'}], max_tokens=4000, temperature=0.0)
    print('PROBE_OK' if r.strip() else 'PROBE_EMPTY')
except ApiError as e:
    print('PROBE_FAIL', str(e)[:100])
"
}

ok=0
while true; do
  out=$(probe | tail -1)
  echo "проба: $out ($(date +%H:%M))" >> "$LOG"
  case "$out" in PROBE_OK*) ok=1; break;; esac
  # 429 бесплатен: стучимся каждые 30 мин, пока бюджет не оживёт (сброс/пополнение)
  sleep 1800
done
[ "$ok" -eq 1 ] || exit 1

FILES="eval/history/v21.submission.csv eval/history/v22.submission.csv eval/history/dense.submission.csv eval/history/head.submission.csv"
for attempt in 1 2 3; do
  "$PY" -X utf8 eval/judge.py score $FILES >> "$LOG" 2>&1
  rc=$?
  echo "judge: попытка $attempt rc=$rc" >> "$LOG"
  [ "$rc" -eq 0 ] && break
  sleep 1800
done

# --- few-shot кандидат: генерация ответов + оценка (если бюджет жив) ---
out=$(probe | tail -1)
echo "проба перед few-shot: $out" >> "$LOG"
case "$out" in
  PROBE_OK*)
    # ключи в окружение для solution.py (он, в отличие от judge.py, .env сам не читает)
    while IFS='=' read -r k v; do
      case "$k" in
        LLM_BASE_URL|LLM_API_KEY|EMBEDDER_API_KEY|EMB_MODEL|LLM_MODEL|RERANKER_API_KEY|RERANKER_MODEL)
          export "$k=$(echo "$v" | sed 's/^"//;s/"$//')" ;;
      esac
    done < <(grep -E '^(LLM_BASE_URL|LLM_API_KEY|EMBEDDER_API_KEY|EMB_MODEL|LLM_MODEL|RERANKER_API_KEY|RERANKER_MODEL)=' .env | sed 's/\r$//')
    unset TOP_K RERANKER_TOP_N MAX_TOKENS MAX_WORKERS TEMPERATURE DENSE SIBLING_FILL 2>/dev/null
    mkdir -p eval/history/cand_fewshot/data
    "$PY" -X utf8 -c "
import zipfile; zipfile.ZipFile(r'build/candidates/submit_head_fewshot.zip').extractall(r'eval/history/cand_fewshot')
"
    cp -f data/demo_questions.csv eval/history/cand_fewshot/data/questions.csv
    (cd eval/history/cand_fewshot && DATA_DIR=data "$PY" -X utf8 solution.py) >> "$LOG" 2>&1
    if [ -f eval/history/cand_fewshot/submission.csv ]; then
      cp -f eval/history/cand_fewshot/submission.csv eval/history/fewshot.submission.csv
      "$PY" -X utf8 eval/judge.py score eval/history/fewshot.submission.csv >> "$LOG" 2>&1 \
        || echo "judge few-shot: не прошёл" >> "$LOG"
    else
      echo "few-shot: solution.py не создал submission.csv" >> "$LOG"
    fi
    ;;
  *) echo "бюджет кончился — few-shot пропущен" >> "$LOG" ;;
esac

"$PY" -X utf8 eval/history/report.py >> "$LOG" 2>&1
echo "=== overnight: готово $(date) ===" >> "$LOG"
