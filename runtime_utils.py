# -*- coding: utf-8 -*-
"""Тайминги этапов (п.15), кэши (п.11), бюджет времени на вопрос (п.13/20)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from typing import Any, Callable, Optional

from textutil import sha1


class StageStats:
    """Потокобезопасная агрегация времени по этапам: n / сумма / max."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n: dict[str, int] = defaultdict(int)
        self._sum: dict[str, float] = defaultdict(float)
        self._max: dict[str, float] = defaultdict(float)
        self.counters: dict[str, int] = defaultdict(int)

    def add(self, stage: str, seconds: float) -> None:
        with self._lock:
            self._n[stage] += 1
            self._sum[stage] += seconds
            self._max[stage] = max(self._max[stage], seconds)

    def count(self, name: str, inc: int = 1) -> None:
        with self._lock:
            self.counters[name] += inc

    def summary(self) -> str:
        with self._lock:
            rows = sorted(self._sum.items(), key=lambda kv: -kv[1])
            parts = [f"{k}: n={self._n[k]} Σ={v:.1f}s avg={v / self._n[k]:.2f}s max={self._max[k]:.1f}s"
                     for k, v in rows]
            cnt = ", ".join(f"{k}={v}" for k, v in sorted(self.counters.items()))
        return "Этапы: " + " | ".join(parts) + (f"\nСчётчики: {cnt}" if cnt else "")


STATS = StageStats()


class Trace:
    """Тайминги одного вопроса; одновременно кормит глобальный STATS."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self.notes: list[str] = []

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self.stages[name] = self.stages.get(name, 0.0) + dt
            STATS.add(name, dt)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


class LRU:
    """Потокобезопасный LRU с single-flight: одинаковые ключи считаются один раз."""

    def __init__(self, maxsize: int = 2048) -> None:
        self.maxsize = maxsize
        self._d: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                self.hits += 1
                return self._d[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def get_or_compute(self, key: str, fn: Callable[[], Any], wait_s: float = 120.0) -> Any:
        val = self.get(key)
        if val is not None:
            return val
        with self._lock:
            ev = self._inflight.get(key)
            leader = ev is None
            if leader:
                ev = self._inflight[key] = threading.Event()
        if not leader:
            ev.wait(wait_s)
            val = self.get(key)
            if val is not None:
                return val
            return fn()
        try:
            val = fn()
            if val is not None:
                self.put(key, val)
            return val
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            ev.set()


class DiskCache:
    """Простой файловый кэш JSON (ключ → значение); сбои записи игнорируются."""

    def __init__(self, directory: str, namespace: str) -> None:
        base = directory or os.path.join(tempfile.gettempdir(), "meridian_cache")
        self.dir = os.path.join(base, namespace)
        self.ok = True
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError:
            self.ok = False

    def _path(self, key: str) -> str:
        return os.path.join(self.dir, sha1(key)[:40] + ".json")

    def get(self, key: str) -> Optional[Any]:
        if not self.ok:
            return None
        try:
            with open(self._path(key), encoding="utf-8") as f:
                rec = json.load(f)
            return rec["v"] if rec.get("k") == key else None
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, value: Any) -> None:
        if not self.ok:
            return
        try:
            tmp = self._path(key) + f".{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"k": key, "v": value}, f, ensure_ascii=False)
            os.replace(tmp, self._path(key))
        except OSError:
            pass


class Budget:
    """Адаптивные уровни деградации по оставшемуся времени.

    allowance = (дедлайн − сейчас) × workers / осталось_вопросов.
    level 0 — полный конвейер; 1 — без судьи и регенерации; 2 — без реранка и
    переформулировки; 3 — только экстрактивный ответ.
    """

    def __init__(self, deadline: float, total: int, workers: int) -> None:
        self.deadline = deadline
        self.total = max(1, total)
        self.workers = max(1, workers)
        self._done = 0
        self._lock = threading.Lock()

    def mark_done(self) -> None:
        with self._lock:
            self._done += 1

    def level(self) -> int:
        remaining_q = max(1, self.total - self._done)
        left = self.deadline - time.time()
        if left <= 0:
            return 3
        allowance = left * self.workers / remaining_q
        if allowance < 6:
            return 3
        if allowance < 14:
            return 2
        if allowance < 28:
            return 1
        return 0
