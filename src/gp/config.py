"""Settings from the environment / .env file. Nothing personal is ever hard-coded."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1"


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    contact: str
    default_rsn: str | None

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gp.duckdb"

    @property
    def user_agent(self) -> str:
        contact = f"contact: {self.contact}" if self.contact else "https://github.com/lucasfazzib/gielinor-planner"
        return f"gielinor-planner/{VERSION} ({contact})"


@cache
def settings() -> Settings:
    _load_dotenv()
    return Settings(
        data_dir=Path(os.environ.get("GP_DATA_DIR", ROOT / "data")),
        contact=os.environ.get("GP_CONTACT", "").strip(),
        default_rsn=os.environ.get("GP_RSN") or None,
    )
