from enum import StrEnum
from pydantic import BaseModel, constr


class Role(StrEnum):
    SYSTEM = "system"
    ASSISTANT = "assistant"
    USER = "user"


class ChainType(StrEnum):
    DOCS = "docs"
    MAIN = "main"


class Message(BaseModel):
    role: constr(
        pattern=f"^({Role.ASSISTANT}|{Role.USER}|{Role.SYSTEM})$"
    )  # noqa: E501
    content: str


class ChatRequest(BaseModel):
    chain_type: ChainType = ChainType.DOCS
    messages: list[Message]


class HealthCheck(BaseModel):
    status: str = "OK"


class TokenUpdate(BaseModel):
    ROLLING_TOKEN: str


class TokenUpdateResponse(BaseModel):
    message: str
