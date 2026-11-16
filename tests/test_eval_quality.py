"""Offline acceptance regressions. Run with python -m unittest; no project imports."""
import io
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from quality import (check_assertions, fact_in_evidence, number_keys,
                     number_unit_pairs, read_csv, retained_fragments, score_answer)


class QualityTests(unittest.TestCase):
    def test_period_swap_is_rejected(self):
        gold = "Приёмка — 5 рабочих дней; оплата — 30 календарных дней."
        bad = "Приёмка — 5 календарных дней; оплата — 30 рабочих дней."
        self.assertFalse(score_answer(bad, gold)["units"])
        self.assertTrue(score_answer(gold, gold)["units"])

    def test_extra_amount_is_review_not_silently_accepted(self):
        result = score_answer("Плата 450 000 рублей; обеспечение 900 000 рублей.", "Плата 450 000 рублей.")
        self.assertTrue(result["numbers"])
        self.assertTrue(result["needs_review"])
        self.assertIn("900000", result["extra_numbers"])
        self.assertTrue(result["diagnostic_only"])

    def test_empty_answer_cannot_have_perfect_score(self):
        self.assertFalse(score_answer("", "")['nonempty'])
        self.assertLess(score_answer("", "")['score'], 1)

    def test_unjustified_refusal_is_flagged(self):
        self.assertFalse(score_answer("Не могу ответить. Плата 450 000 рублей.", "Плата 450 000 рублей.")["refusal"])

    def test_supported_refusal_is_accepted(self):
        self.assertTrue(score_answer("Недостаточно данных.", "В базе нет документов о пособии.")["refusal"])

    def test_spelled_numbers_and_parentheses(self):
        self.assertEqual(number_unit_pairs("3 (трёх) рабочих дня"), {("3", "business_days")})
        self.assertEqual(number_unit_pairs("в течение трёх рабочих дней"), {("3", "business_days")})
        self.assertEqual(number_unit_pairs("шести месяцев"), {("6", "months")})

    def test_amount_groups_and_decimals(self):
        self.assertIn("450000", number_keys("450\u00a0000 рублей"))
        self.assertIn("0.1", number_keys("0,1%"))
        self.assertIn("12000000", number_keys("12 млн"))
        self.assertIn("500000", number_keys("0,5 млн"))

    def test_word_inside_other_word_is_not_a_unit(self):
        self.assertEqual(number_unit_pairs("В части 5 документа"), set())

    def test_negated_included_costs_are_rejected(self):
        record = {"id": "q16", "assertions": [{"id": "included", "any_of": [r"включ", r"учтен"],
                  "forbidden": [r"не\s+включ"]}]}
        self.assertTrue(check_assertions("Расходы уже учтены в цене.", record)["rule_pass"])
        self.assertFalse(check_assertions("Расходы не включены в цену.", record)["rule_pass"])

    def test_missing_specific_day_count_is_rejected(self):
        record = {"id": "q18", "required_pairs": [["3", "business_days"]]}
        self.assertTrue(check_assertions("В течение трёх рабочих дней.", record)["rule_pass"])
        self.assertFalse(check_assertions("В течение пяти рабочих дней.", record)["rule_pass"])
        self.assertFalse(check_assertions("В течение 3 календарных дней.", record)["rule_pass"])

    def test_lexical_rules_accept_spelled_parenthetical_numbers(self):
        record={'id':'deadline','assertions':[{'id':'term','any_of':[r'5\s*рабоч|пяти\s*рабоч']}]}
        self.assertTrue(check_assertions('В течение 5 (пяти) рабочих дней.',record)['rule_pass'])
        self.assertFalse(check_assertions('В течение 5 (пяти) календарных дней.',record)['rule_pass'])

    def test_wrong_document_is_rejected_by_explicit_rule(self):
        record = {"id": "source", "assertions": [{"id": "doc", "all_of": [r"договор a"],
                  "forbidden": [r"договор b"]}]}
        self.assertTrue(check_assertions("Договор A, п. 3.1.", record)["rule_pass"])
        self.assertFalse(check_assertions("Договор B, п. 3.1.", record)["rule_pass"])

    def test_manual_review_is_never_hidden(self):
        result = check_assertions("Да", {"id": "x", "original_sources":[{'file':'source.txt'}],
                                          "assertions": [{"id": "meaning", "manual_review": True}]})
        self.assertTrue(result["rule_pass"])
        self.assertEqual(result["manual_review"], ['claim_source_binding',"meaning"])
        self.assertNotEqual(result["semantic_verdict"], "passed")

    def test_fact_before_crop_is_visible(self):
        c = [{"chunk_id": "a", "text_ctx": "Плата 450 000 рублей. " + "x" * 100}]
        self.assertTrue(fact_in_evidence(re.compile("450 000"), c, 64, 1000))

    def test_fact_after_crop_is_not_visible(self):
        c = [{"chunk_id": "a", "text_ctx": "x" * 100 + " Плата 450 000 рублей."}]
        self.assertFalse(fact_in_evidence(re.compile("450 000"), c, 64, 1000))

    def test_whole_chunk_excluded_by_budget(self):
        c = [{"chunk_id": "a", "text_ctx": "x" * 60}, {"chunk_id": "b", "text_ctx": "Плата 450 000 рублей."}]
        self.assertFalse(fact_in_evidence(re.compile("450 000"), c, 64, 64))

    def test_budget_boundary_includes_exact_size(self):
        self.assertEqual(retained_fragments([{"chunk_id": "a", "text": "abcd"}], 4, 4), {"a": "abcd"})

    def test_notes_are_separate_evidence(self):
        self.assertTrue(fact_in_evidence(re.compile("10 000"), [], 64, 64, ["Порог 10 000."]))

    def test_wrong_document_does_not_support_fact(self):
        c = [{"chunk_id": "a", "doc_id": "B", "text": "450 000 рублей"}]
        self.assertFalse(fact_in_evidence(re.compile("450 000"), c, 64, 64, source_doc_ids={"A"}))

    def test_notes_without_provenance_cannot_bypass_source_restriction(self):
        self.assertFalse(fact_in_evidence(re.compile("450 000"), [], 64, 64, ["450 000"], {"A"}))

    def test_xml_escape_changes_actual_budget(self):
        c = [{"chunk_id": "a", "text": "</x>"}]
        self.assertEqual(retained_fragments(c, 4, 4), {})

    def csv_read(self, content):
        with patch.object(Path, 'open', return_value=io.StringIO(content)):
            return read_csv(Path('fixture.csv'))

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            self.csv_read('id,answer\n1,a\n1,b\n')

    def test_extra_columns_are_rejected(self):
        with self.assertRaises(ValueError):
            self.csv_read('id,answer,extra\n1,a,x\n')

    def test_empty_answers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.csv_read('id,answer\n1,\n')

    def test_quoted_csv_and_all_ids_are_preserved(self):
        self.assertEqual(self.csv_read('id,answer\n1,"a,b"\n2,"строка\nещё"\n'), {'1': 'a,b', '2': 'строка\nещё'})

    def test_live_evaluator_import_does_not_load_assistant_or_read_env(self):
        path = Path(__file__).resolve().parents[1] / 'eval/real_eval.py'
        spec = importlib.util.spec_from_file_location('review_real_eval', path)
        module = importlib.util.module_from_spec(spec)
        with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected file read')):
            spec.loader.exec_module(module)
        self.assertFalse(module.score_answer('5 календарных дней', '5 рабочих дней', '')['units'])

    def test_coverage_module_is_importable_without_project_dependencies(self):
        import fact_coverage
        facts = fact_coverage.facts_of('Срок — 5 рабочих дней; гарантия — 5 месяцев.')
        self.assertEqual(len([f for f in facts if f['key']=='5']), 2)

    def test_coverage_number_is_not_prefix_of_larger_amount(self):
        import fact_coverage
        fact = {'pat': fact_coverage._num_pattern('450 000', 'рублей')}
        self.assertTrue(fact_coverage.fact_matches(fact, '450 000 рублей'))
        self.assertFalse(fact_coverage.fact_matches(fact, '450 0000 рублей'))

    def test_all_trap_positive_and_negative_examples(self):
        path = Path(__file__).resolve().parents[1] / 'eval/traps.jsonl'
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            with self.subTest(trap=row['id']):
                self.assertTrue(check_assertions(row['expected_answer'], row)['rule_pass'])
                self.assertFalse(check_assertions(row['incorrect_answer_example'], row)['rule_pass'])

    def test_larger_amount_containing_correct_digits_is_rejected(self):
        path = Path(__file__).resolve().parents[1] / 'eval/traps.jsonl'
        row = next(json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()
                   if json.loads(s)['id']=='draft-rent-amount')
        self.assertFalse(check_assertions('Нет. Плата 1 450 000 рублей в месяц, включая НДС.', row)['rule_pass'])

    def test_penalty_must_apply_cap_to_final_result(self):
        path = Path(__file__).resolve().parents[1] / 'eval/traps.jsonl'
        row = next(json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()
                   if json.loads(s)['id']=='authored-penalty-cap')
        bad = 'Ставка 0,1%, предел 10% = 10 000 рублей, но итоговая неустойка — 20 000 рублей.'
        self.assertFalse(check_assertions(bad, row)['rule_pass'])


if __name__ == '__main__':
    unittest.main()
