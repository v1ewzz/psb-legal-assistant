# -*- coding: utf-8 -*-
"""Тесты рычагов №2 (verify v2: пара число+единица в одном предложении
evidence) и №3 (детерминированный render: трим по предложению, «Источник:»)."""
from config import from_env
from pipeline import (_finalize, _render_refs, _unit_pair_problem, _unit_pairs,
                      _verify_lite)

# Реальные формулировки demo-базы (п. 3.1 аренды, п. 4.2 поставки, п. 3.2 регламента)
EV_RENT = ("3.1. Арендная плата составляет 450 000 (четыреста пятьдесят тысяч) "
           "рублей в месяц, включая НДС.")
EV_DAYS = ("4.2. Покупатель оплачивает Товар в течение 30 (тридцати) календарных "
           "дней с даты подписания универсального передаточного документа (УПД).")
EV_KEEP = ("3.2. Подписанный оригинал договора передаётся в юридический отдел "
           "для хранения в течение 3 (трёх) рабочих дней с даты подписания.")
EV_PENYA = "5.2. При просрочке Покупатель уплачивает пени в размере 0,1% в день от суммы задолженности."


# ---------- рычаг №2: _unit_pair_problem ----------

def test_golden_style_passes():
    """Легитимное слияние пунктов + скобочная расшифровка + «в месяц»."""
    frag = [EV_RENT, EV_DAYS]
    ans = ("Арендная плата — 450 000 рублей в месяц, включая НДС (п. 3.1); "
           "оплата — в течение 30 календарных дней с даты подписания УПД (п. 4.2).")
    assert _unit_pair_problem(ans, frag) is None


def test_unit_swap_caught():
    """Находка аудита Никиты: «5 рабочих дней» → «5 календарных дней».

    Глобальная проверка чисел это пропускает (5 есть в evidence)."""
    frag = [EV_KEEP]
    ans = "Оригинал передаётся в течение 3 календарных дней с даты подписания."
    assert _verify_lite(ans, " ".join(frag)) is None          # глобальная — молчит
    assert _unit_pair_problem(ans, frag) is not None          # пара — ловит


def test_cross_fragment_unit_leak_caught():
    """Число из одного фрагмента + единица из другого: «450 000 рублей в неделю»."""
    frag = [EV_RENT, "7.1. Хранение документации — 10 рабочих дней в неделю."]
    ans = "Арендная плата составляет 450 000 рублей в неделю."
    assert _unit_pair_problem(ans, frag) is not None


def test_percent_period_swap_caught():
    frag = [EV_PENYA]
    ans = "Пени — 0,1% в час от суммы задолженности."
    assert _unit_pair_problem(ans, frag) is not None
    assert _unit_pair_problem("Пени — 0,1% в день от суммы.", frag) is None


def test_vaguer_rephrasing_passes():
    """Ответ менее конкретен, чем evidence («30 дней» при «30 календарных») — ок."""
    assert _unit_pair_problem("Оплата — в течение 30 дней.", [EV_DAYS]) is None


def test_added_specificity_caught():
    """Ответ добавляет конкретику, которой нет в evidence («руб./мес» при «руб»)."""
    frag = ["3.1. Плата составляет 450 000 рублей."]
    ans = "Плата составляет 450 000 рублей в месяц."
    assert _unit_pair_problem(ans, frag) is not None


def test_years_excluded():
    """Годы 1900–2100 не проверяем (даты «01.08.2024», эталон Q5)."""
    frag = ["2.4. Договоры на сумму свыше 10 000 000 рублей одобряются до подписания.",
            "Регламент в редакции от 01.03.2025."]
    ans = "В августе 2024 года действовал порог 10 000 000 рублей."
    assert _unit_pair_problem(ans, frag) is None


def test_bare_numbers_ignored_by_pair_check():
    """Число без единицы — зона глобальной проверки, пара не строится."""
    assert _unit_pairs("Пункт 5.1 применяется к партии 3.") == set()
    assert _unit_pair_problem("Применяется пункт 5.1 к партии 3.", ["5.1. Партия 3."]) is None


def test_no_digits_no_problem():
    assert _unit_pair_problem("По памяти отвечать не буду.", [EV_RENT]) is None


# ---------- рычаг №3: детерминированный render ----------

LONG = ("Арендная плата составляет 450 000 рублей в месяц, включая НДС. "
        "Плата вносится ежемесячно не позднее 5-го числа текущего месяца. "
        "При просрочке уплачиваются пени 0,1% в день. ") * 3  # ~640 симв.


def test_render_trims_at_sentence_boundary():
    out = _finalize(LONG, det=True)
    assert len(out) <= 500
    assert out.endswith(".")            # граница предложения, не «…» посреди слова
    assert "Источник" not in out        # одна ссылка — строки нет


def test_render_appends_source_line():
    ans = ("Плата — 450 000 рублей в месяц, включая НДС (п. 3.1), срок оплаты — "
           "30 календарных дней (п. 4.2). Договор № А-08/2024 действует с 01.08.2024.")
    out = _finalize(ans, det=True)
    assert "Источник:" in out
    assert "п. 3.1" in out and "п. 4.2" in out


def test_render_source_rules():
    one_ref = "Плата составляет 450 000 рублей в месяц (п. 3.1)."
    assert "Источник" not in _finalize(one_ref, det=True)      # <2 ссылок
    explicit = "Ответ по п. 3.1 и п. 3.2. Источник: база компании."
    assert _finalize(explicit, det=True).count("Источник") == 1  # не дублируем


def test_render_off_by_default_flag():
    assert "Источник" not in _finalize("Ответ по п. 3.1 и п. 3.2.", det=False)
    assert len(_finalize(LONG, det=False)) > 500                # старый путь


def test_render_refs_order_and_dedup():
    refs = _render_refs("По п. 3.1 и № А-08/2024 (п. 3.1), 54-ФЗ, № А-08/2024.")
    assert refs[0] == "п. 3.1"
    assert refs.count("№ А-08/2024") == 1
    assert "54-ФЗ" in refs


# ---------- флаги ----------

def test_flags_default_on_and_env_off():
    assert from_env({}).verify_v2 is True
    assert from_env({}).render_det is True
    assert from_env({"VERIFY_V2": "0"}).verify_v2 is False
    assert from_env({"RENDER_DET": "0"}).render_det is False
