"""Load API keys from the secrets file that lives outside the repository.

Default path: C:\\Users\\<you>\\.golem\\secrets.env (override with GOLEM_SECRETS).
A real environment variable with the same name wins over the file.
Values are never printed.
"""

import os
from pathlib import Path

SECRETS_PATH = Path(os.environ.get("GOLEM_SECRETS", Path.home() / ".golem" / "secrets.env"))


def _read_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def get_secret(name: str) -> str:
    """The value for `name`, or a clear error saying where to put it."""
    value = os.environ.get(name) or _read_file(SECRETS_PATH).get(name, "")
    if not value:
        raise SystemExit(f"{name} is empty. Paste it after '{name}=' in {SECRETS_PATH}.")
    return value
