from enum import StrEnum
from pydantic import BaseModel, constr, Field
from typing_extensions import List, TypedDict
from langchain.schema import Document


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


# Meta generator schema
class FieldMetadata(BaseModel):
    name: str = Field(description="The name of the field")
    title_en: str = Field(description="The title of the field in English")
    title_ms: str = Field(description="The title of the field in Malay")
    description_en: str = Field(description="The description of the field in English")
    description_ms: str = Field(description="The description of the field in Malay")


class DatasetMetadata(BaseModel):
    title_en: str = Field(description="The title of the dataset in English")
    title_ms: str = Field(description="The title of the dataset in Malay")
    description_en: str = Field(description="The description of the dataset in English")
    description_ms: str = Field(description="The description of the dataset in Malay")
    methodology_en: str = Field(description="The methodology of the dataset in English")
    methodology_ms: str = Field(description="The methodology of the dataset in Malay")
    caveats_en: str = Field(description="The caveats of the dataset in English")
    caveats_ms: str = Field(description="The caveats of the dataset in Malay")
    fields: list[FieldMetadata] = Field(description="The fields of the dataset")


class State(TypedDict):
    input_data: dict
    similar_datasets: List[Document]


class OutputState(TypedDict):
    answer: DatasetMetadata


class GenerateMetaRequest(BaseModel):
    input_data: dict


class GenerateMetaResponse(BaseModel):
    metadata: DatasetMetadata
