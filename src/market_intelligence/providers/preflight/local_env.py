from __future__ import annotations

import os
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ENV_FILE = REPOSITORY_ROOT / ".env"
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_env_file(path: Path, *, required: bool) -> dict[str, str]:
    if not path.exists():
        if required:
            raise ValueError("the requested environment file does not exist")
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ValueError(f"invalid environment entry on line {line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not ENV_NAME.fullmatch(name):
            raise ValueError(f"invalid environment name on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def load_environment(env_file: Path | None) -> dict[str, str]:
    path = env_file or DEFAULT_ENV_FILE
    values = read_env_file(path, required=env_file is not None)
    values.update(os.environ)
    return values
