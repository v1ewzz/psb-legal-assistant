# -*- coding: utf-8 -*-
"""Сборка сабмит-ZIP (§2.3, §22.2): ТОЛЬКО корень архива, без вложенных папок.

В ZIP: solution.py + плоские модули; requirements.txt (pinned); артефакты
индекса в корне (chunks.jsonl.gz, docs.json, refs.json, bm25.index.npz,
manifest.json); нет секретов/.env; размер ≤128 МБ.

Использование: py scripts/make_submit.py [--outdir build]
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODULES = [
    "solution.py",
    "pipeline.py",
    "config.py",
    "llm_client.py",
    "ingest.py",
    "retrieval.py",
    "guard.py",     # v2: парсер диалога, сущности, эвристический standalone
    "temporal.py",  # v2: даты события, окна редакций (D1), заметки Reason
    "requirements.txt",
]

ARTIFACTS = [
    "chunks.jsonl.gz",
    "docs.json",
    "refs.json",
    "bm25.index.npz",
    "manifest.json",
]

MAX_ZIP_BYTES = 128 * 1024 * 1024

RUNTIME_MARKERS = ("# Веб-интерфейс", "# Dev")


def _runtime_requirements(path: Path) -> str:
    """В сабмит — только раннтайм-блок (до маркеров dev/web/py; §2.6)."""
    lines: list[str] = []
    in_runtime = True
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(RUNTIME_MARKERS):
            in_runtime = False
        if in_runtime:
            lines.append(line)
    return "\n".join(lines) + "\n"


def collect(outdir: Path) -> list[Path]:
    paths: list[Path] = []
    missing = []
    for name in MODULES + ARTIFACTS:
        for base in (ROOT, ROOT / "index"):
            if (base / name).is_file():
                paths.append(base / name)
                break
        else:
            missing.append(name)
    if missing:
        raise FileNotFoundError(
            "нет файлов: " + ", ".join(missing) +
            " (индекс собирается: py ingest.py --build-index)"
        )
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default="build")
    args = parser.parse_args()
    outdir = (ROOT / args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    zip_path = outdir / "submit.zip"

    paths = collect(outdir)
    names = [p.name for p in paths]
    if len(names) != len(set(names)):
        sys.exit("Дубли имён в сабмите!")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            if path.name == "requirements.txt":
                zf.writestr(path.name, _runtime_requirements(path))
            else:
                zf.write(path, arcname=path.name)  # arcname = имя в корне (§2.3)

    size = zip_path.stat().st_size
    if size > MAX_ZIP_BYTES:
        sys.exit(f"ZIP {size} байт — превышает лимит 128 МБ")
    with zipfile.ZipFile(zip_path) as zf:
        bad = [n for n in zf.namelist() if "/" in n or "\\" in n or n.startswith("__MACOSX")]
        secrets = [n for n in zf.namelist() if n.lower().endswith(".env")]
        if bad or secrets:
            sys.exit(f"Запрещённые пути/файлы: {bad + secrets}")
        print(f"Сабмит собран: {zip_path} ({size / 1024:.0f} КБ), файлов: {len(zf.namelist())}")
        for n in zf.namelist():
            print("  -", n)


if __name__ == "__main__":
    main()
