# -*- coding: utf-8 -*-
"""Reason: генерация ответа, evidence-mapping по подвопросам (§12).

Фрагменты в разделителях <fragment id="cNN">, инструкции внутри не выполнять.
Каждый claim помечен [cNN] и несёт ≥1 валидный cite; статусы supported/partial/
absent + missing. Финальный текст собирает render.py, не LLM (D3).
"""
# TODO(Фаза 3): Reason-вызов + JSON-схема + repair.
