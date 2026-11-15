#!/usr/bin/env bash
# Реплей исторических сабмитов на demo-18 в платформенных условиях:
# env = только ключи/модели (как передаёт платформа), без локальных ручек.
# Результат: eval/history/<name>.submission.csv + .log
set -u
cd /d/hackaton_PSB
mkdir -p eval/history .replay

# --- минимальное окружение (секреты не печатаем) ---
while IFS='=' read -r k v; do
  case "$k" in
    LLM_BASE_URL|LLM_API_KEY|EMBEDDER_API_KEY|EMB_MODEL|LLM_MODEL|RERANKER_API_KEY|RERANKER_MODEL)
      export "$k=$(echo "$v" | sed 's/^"//;s/"$//')" ;;
    DATA_DIR) : ;;  # задаём per-worktree ниже
  esac
done < <(grep -E '^(LLM_BASE_URL|LLM_API_KEY|EMBEDDER_API_KEY|EMB_MODEL|LLM_MODEL|RERANKER_API_KEY|RERANKER_MODEL|DATA_DIR)=' .env | sed 's/\r$//')
unset TOP_K RERANKER_TOP_N MAX_TOKENS MAX_WORKERS TEMPERATURE DENSE SIBLING_FILL FAST_MODE T5_GATE GATES 2>/dev/null
export PYTHONIOENCODING=utf-8
PY=/d/hackaton_PSB/.venv/Scripts/python.exe

run_state() {  # run_state <name> <sha>
  local name=$1 sha=$2
  local wt=.replay/$name
  echo "===== [$name] $sha ====="
  if [ ! -d "$wt" ]; then
    git worktree add --detach "$wt" "$sha" >/dev/null 2>&1 || { echo "worktree fail"; return; }
  fi
  mkdir -p "$wt/data"
  cp -f data/knowledge-base.zip "$wt/data/kb.zip"
  cp -f data/demo_questions.csv "$wt/data/questions.csv"
  echo "-- build index"
  (cd "$wt" && "$PY" -X utf8 ingest.py --build-index --kb data/kb.zip) || echo "INDEX FAIL"
  echo "-- solve"
  (cd "$wt" && DATA_DIR=data "$PY" -X utf8 solution.py) 2>&1 | tail -5
  if [ -f "$wt/submission.csv" ]; then
    cp -f "$wt/submission.csv" "eval/history/$name.submission.csv"
    echo "OK -> eval/history/$name.submission.csv ($(wc -l < "$wt/submission.csv") строк)"
  else
    echo "SOLVE FAIL: нет submission.csv"
  fi
}

run_state n1    37db356
run_state rules c5d240f
run_state v2    6c9fade
run_state v21   eca3a63
run_state v22   60cd972
run_state dense 35d0099
run_state head  da0955a
echo "===== REPLAY DONE ====="
