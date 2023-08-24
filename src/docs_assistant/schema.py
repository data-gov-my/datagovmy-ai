from enum import StrEnum
from pydantic import BaseModel, constr


class Role(StrEnum):
    SYSTEM = "system"
    ASSISTANT = "assistant"
    USER = "user"


class Message(BaseModel):
    role: constr(regex=f"^({Role.ASSISTANT}|{Role.USER}|{Role.SYSTEM})$")  # noqa: E501
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


class HealthCheck(BaseModel):
    status: str = "OK"


class TokenUpdate(BaseModel):
    ROLLING_TOKEN: str


class TokenUpdateResponse(BaseModel):
    message: str
