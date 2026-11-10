# -*- coding: utf-8 -*-
"""Temporal-слой (D1): окна редакций, версии пунктов, рабочие дни (§11).

«Сегодня» = дата выгрузки 2026-10-10. Статусы: ok / ok_unspecified /
edition_missing_past / edition_future / edition_unknown_past.
Документы компании: doc_date ≠ valid_from (К-12), applies_from ДС (T4).
Сроки/числа считает код (§11.3); производственный календарь неизвестен.
"""
# TODO(Фаза 3): таблица тестов в tests/test_temporal.py — 15+ дат-кейсов.
