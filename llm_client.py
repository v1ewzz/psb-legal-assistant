# -*- coding: utf-8 -*-
"""API-клиент: chat() / embed() / rerank() / probe_all() / мок-режим (§2.5).

Контракты: POST /chat/completions, POST /embeddings (маппить по index,
L2-нормализация на клиенте, батчи ≤64), POST /rerank (results|data).
Ретраи: 1–2 на 429/5xx/сеть, пауза 1–2 с; тайм-аут 30–45 с (не 90).
"""
# TODO(Фаза 0): реализовать три вызова + probe_all() + интеграционные тесты против мока.
