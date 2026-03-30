from __future__ import annotations

import os
from collections.abc import Iterable, MutableMapping
from pathlib import Path


def _candidate_roots(start: Path | None = None) -> Iterable[Path]:
    current = (start or Path.cwd()).resolve()
    seen: set[Path] = set()
    for path in (current, *current.parents):
        if path not in seen:
            seen.add(path)
            yield path

    project_root = Path(__file__).resolve().parents[3]
    for path in (project_root, *project_root.parents):
        if path not in seen:
            seen.add(path)
            yield path


def find_env_file(filename: str = ".env", *, start: Path | None = None) -> Path | None:
    for root in _candidate_roots(start):
        candidate = root / filename
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            quote = value[:1]
            value = value[1:-1]
            if quote == '"':
                value = value.encode("utf-8").decode("unicode_escape")
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_env_file(
    path: str | Path | None = None,
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> Path | None:
    target = environ if environ is not None else os.environ
    env_path = Path(path).resolve() if path else find_env_file()
    if env_path is None or not env_path.is_file():
        return None

    for key, value in parse_env_file(env_path).items():
        if override or key not in target:
            target[key] = value
    return env_path
