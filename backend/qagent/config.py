from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///../data/qagent.db"
    data_dir: Path = Path("../data")
    environment: str = "development"

    model_config = SettingsConfigDict(env_prefix="QAGENT_", env_file=".env")


def get_settings() -> Settings:
    return Settings()
