from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Infrastructure config, loaded from environment / .env once at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "llama3.2:3b"
    system_prompt: str = "You are a helpful assistant. Answer concisely."
    temperature: float = 0.7

    host: str = "0.0.0.0"
    port: int = 8000

    # Auth
    secret_key: str = "change-me-in-production"
    session_ttl_hours: int = 24
    db_path: str = "data/users.db"
    admin_email: str = "admin@example.com"
    admin_password: str = "admin"


settings = Settings()
