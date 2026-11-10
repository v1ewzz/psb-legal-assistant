# -*- coding: utf-8 -*-
"""Decide: confidence-фичи + гейты → зона ответа (§14).

Фичи: retrieval (0.30), достаточность (0.25), верификация (0.30), контекст (0.15).
Зоны: special (гейт) / full (conf ≥ T_high) / partial (conf ≥ T_low или
есть supported) / refusal (grounded). Правило §14.3: есть хотя бы один
supported подвопрос → голый отказ недопустим.
"""
# TODO(Фаза 4): калибровка порогов T_high/T_low на eval, логрег на 150–300 примерах.
