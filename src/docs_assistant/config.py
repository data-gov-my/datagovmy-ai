from pydantic import BaseSettings, Field

class Settings(BaseSettings):
    APP_ROOT_PATH: str = Field(..., env='APP_ROOT_PATH')
    OPENAI_API_KEY: str = Field(..., env='OPENAI_API_KEY')
    WEAVIATE_URL: str = Field(..., env='WEAVIATE_URL')
    DOCS_INDEX: str = Field(..., env='DOCS_INDEX')
    DC_META_INDEX: str = Field(..., env='DC_META_INDEX')
    MDX_ROOT_PATH: str = Field(..., env='MDX_ROOT_PATH')
    TELEGRAM_CHAT_ID: str = Field(..., env='TELEGRAM_CHAT_ID')
    TELEGRAM_TOKEN: str = Field(..., env='TELEGRAM_TOKEN')
    GITHUB_TOKEN: str = Field(..., env='GITHUB_TOKEN')
    GITHUB_REPO: str = Field(..., env='GITHUB_REPO')
    GITHUB_PATH: str = Field(..., env='GITHUB_PATH')

    class Config:
        env_file = '.env'

settings = Settings()