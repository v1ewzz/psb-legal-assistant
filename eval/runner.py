# -*- coding: utf-8 -*-
"""Source validation and stored-answer diagnostics, without model/network calls.

Exit 0: declared checks passed; 1: failed checks; 2: blocked/missing input.
No command in this file imports the assistant or calls an API.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

from quality import check_assertions, read_csv, score_answer

ROOT = Path(__file__).resolve().parents[1]


def load_records(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate IDs in {path.name}")
    return rows


def validate_sources(data_dir: Path, gold_path: Path, traps_path: Path) -> dict:
    manifest = json.loads((ROOT / "eval/source_manifest.json").read_text(encoding="utf-8"))
    failures, quotes = [], 0
    for source in manifest["files"]:
        p = data_dir / source["name"]
        if hashlib.sha256(p.read_bytes()).hexdigest() != source["sha256"]:
            failures.append("hash:" + p.name)
    questions = read_csv(data_dir / "demo_questions.csv", "question")
    answers = read_csv(data_dir / "demo_output.csv")
    gold, traps = load_records(gold_path), load_records(traps_path)
    if set(questions) != {r["id"] for r in gold} or set(questions) != set(answers):
        failures.append("demo_id_set_mismatch")
    expected_ids = {str(i) for i in range(1, 19)}
    if set(questions) != expected_ids:
        failures.append("official_demo_must_have_ids_1_to_18")
    corpus = data_dir / "originals"
    if {p.name for p in corpus.glob('*.txt')} != {s['file'] for s in manifest['originals']}:
        failures.append('original_corpus_file_set_mismatch')
    for source in manifest['originals']:
        if hashlib.sha256((corpus/source['file']).read_bytes()).hexdigest()!=source['sha256']:
            failures.append('original_hash:'+source['file'])
    for row in gold + traps:
        label = row["id"]
        if len(row.get("expected_facts", [])) < 3:
            failures.append(label + ":fewer_than_three_facts")
        if not row.get("verification", {}).get("original_txt_verified"):
            failures.append(label + ":unverified_source")
        for assertion in row.get("assertions", []):
            for key in ("all_of", "any_of", "forbidden"):
                for pattern in assertion.get(key, []):
                    re.compile(pattern)
        for source in row.get("original_sources", row.get("sources", [])):
            p = corpus / source["file"]
            if p.resolve().parent != corpus.resolve():
                raise ValueError("source path outside originals")
            raw = p.read_bytes()
            lines = raw.decode("utf-8-sig").splitlines()
            quote = "\n".join(lines[source["line_start"]-1:source["line_end"]])
            if quote != source["quote"] or hashlib.sha256(raw).hexdigest() != source["sha256"]:
                failures.append(label + ":source_quote_mismatch:" + p.name)
            quotes += 1
        answer = row.get("reference_answer", row.get("expected_answer", ""))
        outcome = check_assertions(answer, row)
        failures.extend(label + ":reference:" + f for f in outcome["rule_failures"])
        if "reference_answer" in row and (row["reference_answer"] != answers.get(label)
                                          or row["question"] != questions.get(label)):
            failures.append(label + ":official_demo_mismatch")
        if "calculation" in row:
            c = row["calculation"]
            uncapped = Decimal(c["principal_rub"]) * Decimal(c["rate_percent_per_day"]) / 100 * Decimal(c["days"])
            cap = Decimal(c["principal_rub"]) * Decimal(c["cap_percent"]) / 100
            if uncapped != Decimal(c["uncapped_rub"]) or min(uncapped, cap) != Decimal(c["expected_rub"]):
                failures.append(label + ":calculation_mismatch")
    return {"status": "FAIL" if failures else "PASS", "gold_records": len(gold),
            "trap_records": len(traps), "source_quote_checks": quotes,
            "original_files_checked":len(manifest['originals']),
            "failures": failures, "model_run": False,
            "scope": "dataset provenance and explicit rules, not assistant acceptance"}


def evaluate_saved(path: Path, records: list[dict]) -> dict:
    answers = read_csv(path)
    expected = {r["id"] for r in records}
    if set(answers) != expected:
        raise ValueError(f"{path.name}: missing IDs {sorted(expected-set(answers))}, extra IDs {sorted(set(answers)-expected)}")
    results = []
    for row in records:
        answer = answers[row["id"]]
        result = check_assertions(answer, row)
        reference = row.get("reference_answer", row.get("expected_answer", ""))
        result["diagnostic"] = score_answer(answer, reference, row.get("question", ""))
        results.append(result)
    # These are separate rates; they are not MatchRate or WinRate of an LLM judge.
    return {"file": path.name, "input_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "record_set_sha256":hashlib.sha256(json.dumps(records,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            "assistant_version_for_stored_answers":"not_independently_verified",
            "answers": len(results),
            "rule_pass": sum(r["rule_pass"] for r in results),
            "rule_fail": sum(not r["rule_pass"] for r in results),
            "additional_number_review": sum(r["diagnostic"]["needs_review"] for r in results),
            "semantic_acceptance": "requires_human_review", "model_run": False,
            "results": results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["validate", "facts", "traps", "history"])
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--submission", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    gold = ROOT / "eval/demo_gold.jsonl"
    traps = ROOT / "eval/traps.jsonl"
    try:
        if args.mode == "validate":
            report = validate_sources(args.data_dir, gold, traps)
            code = 0 if report["status"] == "PASS" else 1
        elif args.mode == "history":
            reports = [evaluate_saved(p, load_records(gold))
                       for p in sorted((ROOT / "eval/history").glob("*.submission.csv"))]
            if not reports:
                raise FileNotFoundError("no stored submission files")
            report = {"status": "DIAGNOSTIC", "source": "team-provided stored answers",
                      "official_baseline_verified": False, "reports": reports, "model_run": False}
            code = 0
        else:
            if args.submission is None:
                raise ValueError("--submission is required")
            report = evaluate_saved(args.submission, load_records(traps if args.mode == "traps" else gold))
            code = 1 if report["rule_fail"] else 0
    except FileNotFoundError as error:
        report, code = {"status": "BLOCKED", "reason": str(error), "model_run": False}, 2
    except (ValueError, KeyError, json.JSONDecodeError, re.error) as error:
        report, code = {"status": "FAIL", "reason": str(error), "model_run": False}, 1
    report['evaluator_files_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__),ROOT/'eval/quality.py']}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
