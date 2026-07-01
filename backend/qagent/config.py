from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = WORKSPACE_ROOT / "data"


class Settings(BaseSettings):
    database_url: str = f"sqlite:///{DEFAULT_DATA_DIR / 'qagent.db'}"
    data_dir: Path = DEFAULT_DATA_DIR
    environment: str = "development"
    alpha_vantage_api_key: str | None = None
    fmp_api_key: str | None = None
    finnhub_api_key: str | None = None
    tushare_token: str | None = None
    sec_user_agent: str = "Qagent research app contact@example.com"
    a_share_enhanced_data_enabled: bool = True
    a_share_enhanced_max_cards: int = 10
    a_share_enhanced_min_interval_seconds: float = 1.05
    a_share_enhanced_timeout_seconds: int = 12
    a_share_enhanced_cache_ttl_hours: int = 6

    model_config = SettingsConfigDict(env_prefix="QAGENT_", env_file=".env")


def get_settings() -> Settings:
    return Settings()
