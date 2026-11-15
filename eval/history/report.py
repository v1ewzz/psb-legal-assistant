# -*- coding: utf-8 -*-
"""Итоговый отчёт: судья (LLM-протокол §1.2) × детерминированные фичи × баллы.

Читает eval/history/*.judge.json, пересчитывает детерминированные фичи
(proxy-логика) и печатает/пишет REPORT.md: таблица всех сабмитов,
калибровка формул по известным баллам платформы, прогноз кандидатов.
Работает офлайн (после того как судья отработал).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

HIST = ROOT / "eval" / "history"

PLATFORM_SCORES = {
    "n1": 0.274,
    "rules": 0.208,
    "v2": 0.23688,
    "v21": 0.27615,
    "v22": 0.25032,
    "dense": 0.26199,
}

STATES = ["n1", "rules", "v2", "v21", "v22", "dense", "head",
          "cfc9cad", "cfc9cad_sibling2"]


def fit_and_report(rows: list[dict], lines: list[str]) -> None:
    known = [r for r in rows if r.get("platform") is not None and r.get("judgeM")]
    if len(known) < 3:
        lines.append("Недостаточно данных для калибровки.")
        return
    y = np.array([r["platform"] for r in known])

    def r2(pred):
        err = pred - y
        ss = ((y - y.mean()) ** 2).sum()
        return (1 - (err ** 2).sum() / ss) if ss else float("nan")

    lines.append("### Калибровка по известным баллам (n=%d)" % len(known))
    # судья M -> платформа (однофакторная, масштаб + сдвиг)
    M = np.array([r["judgeM"] for r in known])
    A = np.vstack([M, np.ones_like(M)]).T
    (k, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = k * M + b
    lines.append(f"- Линейная: Score ≈ {k:.3f}·M_judge + {b:.3f}, R²={r2(pred):.3f}, "
                 f"макс|err|={np.abs(pred - y).max():.4f}")
    for r, p in zip(known, pred):
        lines.append(f"  - {r['name']}: платф={r['platform']:.4f} "
                     f"модель={p:.4f} err={p - r['platform']:+.4f}")
    # судья M + W
    W = np.array([r.get("judgeW") if r.get("judgeW") is not None else 0.5
                  for r in known])
    A2 = np.vstack([M, W, np.ones_like(M)]).T
    (k2, w2, b2), *_ = np.linalg.lstsq(A2, y, rcond=None)
    pred2 = k2 * M + w2 * W + b2
    lines.append(f"- Двухфакторная: Score ≈ {k2:.3f}·M + {w2:.3f}·W + {b2:.3f}, "
                 f"R²={r2(pred2):.3f}, макс|err|={np.abs(pred2 - y).max():.4f}")
    # спека без подгонки
    pred3 = 0.7 * M + 0.3 * W
    lines.append(f"- Спека (0.7M+0.3W) как есть: R²={r2(pred3):.3f}")

    cand = [r for r in rows if r.get("platform") is None and r.get("judgeM")]
    if cand:
        lines.append("\n### Прогноз кандидатов")
        for r in cand:
            m, w = r["judgeM"], (r.get("judgeW") if r.get("judgeW") is not None
                                 else 0.5)
            lines.append(f"- **{r['name']}**: M={m:.3f} W={w:.3f} → "
                         f"линейная≈{k * m + b:.4f}, двухфакт≈{k2 * m + w2 * w + b2:.4f}, "
                         f"спека={0.7 * m + 0.3 * w:.4f}")


def main() -> None:
    lines: list[str] = ["# Отчёт: воссоздание формулы судьи (§1.2)", ""]

    lines.append("| сабмит | платформа | M судьи | W судьи | modeM Score | modeS Score | "
                 "ошибок | M детерм | hall% | e.ref% | отказ↑% |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for name in STATES:
        jf = HIST / f"{name}.judge.json"
        row = {"name": name, "platform": PLATFORM_SCORES.get(name)}
        if jf.exists():
            j = json.loads(jf.read_text(encoding="utf-8"))
            row["judgeM"] = j["modeM"]["MatchRate"]
            row["judgeW"] = j["modeM"]["WinRate"]
            row["scoreM"] = j["modeM"]["Score"]
            row["scoreS"] = j["modeS"]["Score"]
            row["errors"] = j.get("errors", 0)
            if j.get("errors", 0) > 30:   # прогон наполовину из заглушек
                row["judgeM"] = row["judgeW"] = None
        rows.append(row)
        plat = row["platform"]
        plat_s = f"{plat:.5f}" if plat is not None else "?"
        jm = f"{row['judgeM']:.3f}" if row.get("judgeM") is not None else "—"
        jw = f"{row['judgeW']:.3f}" if row.get("judgeW") is not None else "—"
        sm = f"{row['scoreM']:.4f}" if row.get("scoreM") is not None else "—"
        ss = f"{row['scoreS']:.4f}" if row.get("scoreS") is not None else "—"
        lines.append(f"| {name} | {plat_s} | {jm} | {jw} | {sm} | {ss} | "
                     f"{row.get('errors', '—')} | детерм. см. proxy.py | | | |")

    lines.append("")
    fit_and_report(rows, lines)

    report = "\n".join(lines)
    (HIST / "REPORT.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
