from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_DEPLOYMENT: str
    AZURE_OPENAI_API_VERSION: str = "2024-12-01-preview"

    PROPROCTOR_WORKSPACE_ID: str
    INFRA_WORKSPACE_ID: str

    COSMOS_ENDPOINT: str
    COSMOS_KEY: str | None = None

    CORS_ORIGINS: str = "http://localhost:3000"
    ANALYZE_TIMEOUT_SECONDS: int = 300
    MAX_AGENT_ITERATIONS: int = 10
    TOOL_RESPONSE_MAX_TOKENS: int = 40000
    LOG_LEVEL: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]
