"""Loads a firm's config file and the secrets in .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
FIRMS_DIR = ROOT / "config" / "firms"


@dataclass
class Firm:
    key: str
    name: str
    timezone: str
    corpus_kind: str
    corpus_path: Path
    brand_handle: str
    data_dir: Path
    public_numbers: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "inbox.db"


def load_firm(key: str) -> Firm:
    path = FIRMS_DIR / f"{key}.yaml"
    if not path.exists():
        known = ", ".join(p.stem for p in FIRMS_DIR.glob("*.yaml"))
        raise SystemExit(f"No firm config {path.name}. Known firms: {known}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    firm = Firm(
        key=key,
        name=raw["firm"]["name"],
        timezone=raw["firm"].get("timezone", "UTC"),
        corpus_kind=raw["corpus"]["kind"],
        corpus_path=ROOT / raw["corpus"]["path"],
        brand_handle=raw["corpus"]["brand_handle"],
        data_dir=ROOT / raw["data_dir"],
        public_numbers=[str(n) for n in raw.get("public_numbers", [])],
        raw=raw,
    )
    firm.data_dir.mkdir(parents=True, exist_ok=True)
    return firm


def env(name: str, default: str | None = None) -> str | None:
    load_dotenv(ROOT / ".env")
    value = os.environ.get(name, default)
    return value or default
