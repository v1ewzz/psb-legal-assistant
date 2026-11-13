# -*- coding: utf-8 -*-
"""Тесты lean-фиксов: A-кламп (сохранение хвоста диалога) и B-lite цитат-линт."""
from pipeline import _citation_problem, clamp_question


def _dialog(n_turns: int) -> str:
    parts = []
    for i in range(n_turns):
        parts.append(f"Пользователь: вопрос номер {i} про договор аренды склада?\n")
        parts.append(f"Ассистент: ответ номер {i} про условия.\n")
    return "".join(parts)


from pipeline import (REFUSAL_PAT_RE, _verify_lite, calc_note, premise_correction,
                      premise_note_text)


EV_PENYA = ("5.2. При просрочке оплаты Покупатель уплачивает пени в размере "
            "0,1% в день от суммы задолженности.")
Q_PENYA = "Какие пени за просрочку оплаты на 10 дней по договору на 450 000 рублей?"


def test_calc_note_basic():
    note, allowed = calc_note(Q_PENYA, EV_PENYA)
    assert note is not None
    assert "4 500" in note          # 450000 × 0,1% × 10 = 4500
    assert "4500" in allowed


def test_calc_note_gate_blocks_wrong_questions():
    assert calc_note("Какой срок приёмки товара на 5 рабочих дней?", EV_PENYA)[0] is None
    assert calc_note(Q_PENYA, "5.1. Качество товара по ГОСТ.")[0] is None
    assert calc_note("Какие пени за просрочку на 10 дней?", EV_PENYA)[0] is None
    assert calc_note(Q_PENYA, "пени 12% в день от суммы")[0] is None


def test_verify_whitelist_for_calc():
    ev = "3.1. Плата 450 000 рублей в месяц."
    ans = "Пени составят 4500 рублей."
    assert _verify_lite(ans, ev, {"4500"}) is None   # с whitelist — ок
    assert _verify_lite(ans, ev) is not None         # без — вычисленное число падает


def test_premise_note():
    chunks = [{"short_name": "Договор аренды № А-08/2024",
               "text": "3.1. Арендная плата составляет 450 000 рублей в месяц, включая НДС."}]
    q = "Почему арендная плата 500 000 рублей в месяц по договору?"
    note = premise_note_text(q, chunks)
    assert note is not None
    assert "500 000" in note and "450 000" in note
    corr = premise_correction(q, chunks)  # гейт-режим T5 по той же детекции
    assert corr is not None and "450 000" in corr


def test_premise_no_false_positive_on_unknown_number():
    chunks = [{"short_name": "Устав", "text": "2.1. Общество отвечает по обязательствам."}]
    assert premise_note_text("Почему устав на 12 страниц?", chunks) is None


def test_refusal_pattern():
    assert REFUSAL_PAT_RE.search("В базе знаний нет документов о порядке.") is not None
    assert REFUSAL_PAT_RE.search("Плата составляет 450 000 рублей.") is None


def test_clamp_noop_short():
    q = "Пользователь: А-08/2024. Можно просто сдать ключи?"
    assert clamp_question(q, 4000) == q


def test_clamp_keeps_last_user_turn():
    cell = _dialog(10)
    assert len(cell) > 300
    clamped = clamp_question(cell, 300)
    assert "вопрос номер 9" in clamped      # последняя реплика цела
    assert "вопрос номер 0" not in clamped  # старая история срезана
    assert len(clamped) <= 300


def test_clamp_huge_single_turn_keeps_tail():
    q = "Пользователь: " + "абзац текста. " * 200
    clamped = clamp_question(q, 200)
    assert len(clamped) <= 200
    assert clamped.endswith(". ") or clamped.endswith(".")


def test_citation_accepts_real_refs():
    ev = "3.1. Арендная плата 450 000 рублей.\n6.3. Возврат по акту."
    assert _citation_problem("Плата 450 000 руб. в месяц (п. 3.1).", ev, "плата?") is None
    assert _citation_problem(
        "Возврат по акту, п. 6.3 (№ А-08/2024).", ev, "№ А-08/2024?") is None


def test_citation_accepts_dash_slash_variants():
    ev = "Договор аренды № А-08-2024 с ООО «Северный Склад».\n6.3. Возврат по акту."
    assert _citation_problem("Срок по договору № А-08/2024 (п. 6.3).", ev, "") is None


def test_citation_catches_fake_refs():
    ev = "3.1. Арендная плата 450 000 рублей."
    assert _citation_problem("Срок 30 дней (п. 4.2).", ev, "") is not None
    assert _citation_problem("По договору № П-17/2024 поставка.", ev, "") is not None
    assert _citation_problem("Обработка по 152-ФЗ разрешена.", ev, "") is not None


def test_citation_whitelists_question():
    assert _citation_problem(
        "По договору № П-17/2024 цена включает доставку.",
        "Цена Товара указывается в Спецификациях.", "договор № П-17/2024") is None


def test_citation_ignores_single_component_refs():
    ev = "ст. 70: испытанию не подлежат. Договор № 14 от 2024 г."
    # «п. 9»/«ст. 70» односоставные — не проверяются (нулевой порог ФП)
    assert _citation_problem("Испытание по ст. 70, правила по п. 9.", ev, "") is None
