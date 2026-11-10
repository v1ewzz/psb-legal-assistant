# -*- coding: utf-8 -*-
"""Пакетное ядро: answer_one(question|реплики_диалога, ctx, deadline) (§5.1).

Конвейер: parse_dialog → Analyze → Retrieve → Temporal → Reason → Verify
→ Decide → Render. Состояние между вопросами не переносится.
"""
# TODO(Фаза 2): pipeline.answer_one в минимальном виде; полный — в Фазе 3.
