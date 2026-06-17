from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://fx:fx@localhost:5432/fx"
    rates_api_url: str = "https://api.exchangeratesapi.io/v1/latest"
    rates_api_key: str = ""
    spread_bps: int = 50
    rate_max_staleness_seconds: int = 300
    quote_ttl_seconds: int = 60


settings = Settings()
