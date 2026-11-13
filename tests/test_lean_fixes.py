# -*- coding: utf-8 -*-
"""Тесты lean-фиксов: A-кламп (сохранение хвоста диалога) и B-lite цитат-линт."""
from pipeline import _citation_problem, clamp_question


def _dialog(n_turns: int) -> str:
    parts = []
    for i in range(n_turns):
        parts.append(f"Пользователь: вопрос номер {i} про договор аренды склада?\n")
        parts.append(f"Ассистент: ответ номер {i} про условия.\n")
    return "".join(parts)


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
