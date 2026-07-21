from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    service_key: str
    lookback_hours: int
    db_path: Path
    host: str
    port: int


def get_settings() -> Settings:
    load_dotenv()
    db_value = os.getenv("DB_PATH", "data/tender_radar.db")
    db_path = Path(db_value)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Settings(
        service_key=os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip(),
        # Three days provides enough current opportunities while staying within
        # the CPU/network budget of the free production instance.
        lookback_hours=int(os.getenv("LOOKBACK_HOURS", "72")),
        db_path=db_path,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8765")),
    )
