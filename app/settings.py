import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass


def _secret(name: str) -> str:
    raw = os.environ.get(name, "") or ""
    cleaned = raw.strip().strip('"').strip("'").replace("\r", "").replace("\n", "")
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    return cleaned


class Settings:
    @property
    def database_url(self) -> str:
        return os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'radar.db'}").strip()

    @property
    def openrouter_key(self) -> str:
        return _secret("OPENROUTER_API_KEY")

    @property
    def fact_model(self) -> str:
        return os.environ.get(
            "OPENROUTER_FACT_MODEL", "anthropic/claude-sonnet-4.6"
        ).strip()

    @property
    def extract_model(self) -> str:
        return os.environ.get(
            "OPENROUTER_EXTRACT_MODEL", "anthropic/claude-sonnet-4.6"
        ).strip()

    @property
    def city_name(self) -> str:
        return os.environ.get("CITY_NAME", "Ribeirão Preto").strip()

    @property
    def demo_mode(self) -> bool:
        return os.environ.get("DEMO_MODE", "1").strip() not in {"0", "false", "False"}

    @property
    def demo_speed(self) -> float:
        # 1.0 = tempo do roteiro; 1.4 deixa a apresentação um pouco mais ágil.
        raw = os.environ.get("DEMO_SPEED", "1.35").strip() or "1.35"
        try:
            return float(raw)
        except ValueError:
            return 1.35

    @property
    def referer(self) -> str:
        return os.environ.get(
            "OPENROUTER_HTTP_REFERER",
            "https://radar-ribeirao.local",
        ).strip()

    @property
    def kb_path(self) -> Path:
        return ROOT / "data" / "kb.json"

    @property
    def programa_path(self) -> Path:
        return ROOT / "data" / "programa.json"


settings = Settings()
