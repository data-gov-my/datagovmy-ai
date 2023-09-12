from enum import Enum
from pydantic import BaseSettings, Field, validator
from typing import List, Union


class AppEnvironment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "prod"


class Settings(BaseSettings):
    ENVIRONMENT: str = Field(AppEnvironment.DEV, env="ENVIRONMENT")
    BACKEND_CORS_ORIGINS: List[str] = Field(..., env="BACKEND_CORS_ORIGINS")
    APP_ROOT_PATH: str = Field(..., env="APP_ROOT_PATH")
    MASTER_TOKEN_KEY: str = Field(..., env="MASTER_TOKEN_KEY")

    OPENAI_API_KEY: str = Field(..., env="OPENAI_API_KEY")
    OPENAI_ORG_ID: str = Field(..., env="OPENAI_ORG_ID")
    WEAVIATE_URL: str = Field(..., env="WEAVIATE_URL")
    DOCS_VINDEX: str = Field(..., env="DOCS_VINDEX")
    DC_META_VINDEX: str = Field(..., env="DC_META_VINDEX")
    DATA_VINDEX: str = Field(..., env="DATA_VINDEX")

    MDX_ROOT_PATH: str = Field(..., env="MDX_ROOT_PATH")

    TELEGRAM_CHAT_ID: str = Field(..., env="TELEGRAM_CHAT_ID")
    TELEGRAM_TOKEN: str = Field(..., env="TELEGRAM_TOKEN")

    GITHUB_TOKEN: str = Field(..., env="GITHUB_TOKEN")
    GITHUB_REPO: str = Field(..., env="GITHUB_REPO")
    GITHUB_PATH: str = Field(..., env="GITHUB_PATH")

    ENCRYPT_KEY: str = Field(default=None, env="ENCRYPT_KEY")
    KEY_FILE: str = Field(default=None, env="KEY_FILE")

    DASH_META_JSON: str = Field(default=None, env="DASH_META_JSON")
    DASH_META_PARQUET: str = Field(default=None, env="DASH_META_PARQUET")

    class Config:
        env_file = ".env"

    @validator("ENVIRONMENT")
    def validate_environment(cls, value):
        if value not in [item.value for item in AppEnvironment]:
            raise ValueError(
                f"Invalid environment. Must be one of: {', '.join([e.value for e in AppEnvironment])}"
            )
        return value

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)


settings = Settings()
