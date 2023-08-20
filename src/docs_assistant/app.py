#!/usr/bin/env python3
import logging
import weaviate
from enum import StrEnum

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from lanarky.responses import StreamingResponse
from lanarky.routing import LangchainRouter, LLMCacheMode
from pydantic import BaseModel, constr
import langchain
from langchain.agents import initialize_agent, Tool, AgentType
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores.weaviate import Weaviate
from langchain.chains import RetrievalQA, RetrievalQAWithSourcesChain
from langchain.chains.qa_with_sources.loading import load_qa_with_sources_chain
from langchain.prompts import PromptTemplate

from config import *
from prompts import *

from dotenv import load_dotenv

load_dotenv()

# maximum history of messages for memory
MAX_MESSAGES = 5

logger = logging.getLogger(__name__)

security = HTTPBearer()


class Role(StrEnum):
    SYSTEM = "system"
    ASSISTANT = "assistant"
    USER = "user"


class Message(BaseModel):
    role: constr(regex=f"^({Role.ASSISTANT}|{Role.USER}|{Role.SYSTEM})$")  # noqa: E501
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    max_tokens: int
    temperature: float


def create_chain(
    messages: list[Message],
):
    from langchain.chat_models import ChatOpenAI
    from langchain.memory import ConversationBufferMemory
    from langchain.memory.chat_message_histories.in_memory import ChatMessageHistory
    from langchain.prompts.chat import (
        ChatPromptTemplate,
        HumanMessagePromptTemplate,
        MessagesPlaceholder,
        SystemMessagePromptTemplate,
    )
    from langchain.schema import (
        AgentAction,
        AgentFinish,
        AIMessage,
        BaseMessage,
        OutputParserException,
        SystemMessage,
    )

    # for caching
    # # TODO: abstract GPT cache stuff
    from gptcache import cache
    from gptcache.embedding import Onnx
    from gptcache.manager import CacheBase, VectorBase, get_data_manager
    from gptcache.similarity_evaluation.distance import SearchDistanceEvaluation
    from gptcache.adapter.langchain_models import LangChainLLMs, LangChainChat
    from gptcache.embedding import OpenAI
    from gptcache.processor.pre import get_messages_last_content

    # get the content(only question) form the prompt to cache
    def get_content_func(data, **_):
        print("get_content_func data", data)
        return data.get("prompt").split("Question")[-1]

    # openai = OpenAI()
    # cache_base = CacheBase('sqlite')
    # vector_base = VectorBase('weaviate', url='http://127.0.0.1:8080', dimension=openai.dimension, class_name='Cache_v1')
    # data_manager = get_data_manager(cache_base, vector_base)
    # cache.init(
    #     pre_embedding_func=get_messages_last_content,
    #     embedding_func=openai.to_embeddings,
    #     data_manager=data_manager,
    #     similarity_evaluation=SearchDistanceEvaluation(),
    #     )
    # cache.set_openai_key()

    chat_prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessagePromptTemplate.from_template(QA_DOCS_ASSISTANT_ALT),
            MessagesPlaceholder(variable_name="history"),
            HumanMessagePromptTemplate.from_template("{query}"),
        ]
    )

    chat_memory = ChatMessageHistory()
    for message in messages:
        if message.role == Role.USER:
            chat_memory.add_user_message(message.content)
        elif message.role == Role.ASSISTANT:
            chat_memory.add_ai_message(message.content)

    memory = ConversationBufferMemory(
        chat_memory=chat_memory,
        return_messages=True,
        memory_key="history",
        input_key="query",
    )

    embedding_llm = OpenAIEmbeddings(
        openai_api_key=settings.OPENAI_API_KEY,
        openai_organization=settings.OPENAI_ORG_ID,
    )

    llm = ChatOpenAI(
        model="gpt-3.5-turbo",
        max_tokens=1000,
        temperature=0,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_organization=settings.OPENAI_ORG_ID,
        streaming=True,
        verbose=True,
    )
    # wrap with GPTCache LLM
    # llm = LangChainChat(chat=llm)

    # connect to weaviate instance
    client = weaviate.Client(url=settings.WEAVIATE_URL)
    attributes = ["header", "source"]
    weaviate_docs = Weaviate(
        client,
        settings.DOCS_INDEX,  # capitalized
        "text",  # constant
        embedding=embedding_llm,
        attributes=attributes,
        by_text=False,  # force vector search
    )

    qa_chain_docs = load_qa_with_sources_chain(
        llm=llm,
        chain_type="stuff",
        # prompt=qa_prompt_docs,
        prompt=chat_prompt,
        memory=memory,
        verbose=True,
    )
    retrival_qa_chain_docs = RetrievalQAWithSourcesChain(
        combine_documents_chain=qa_chain_docs,
        retriever=weaviate_docs.as_retriever(),
        question_key="query",
    )

    return retrival_qa_chain_docs


router = LangchainRouter(llm_cache_mode=LLMCacheMode.IN_MEMORY)
# router = LangchainRouter()


@router.post(
    "/chat",
    summary="OpenAPI Docs Assistant",
    description="Chat with the openAPI documentation assistant",
)
def chat(request: ChatRequest):
    chain = create_chain(messages=request.messages[-MAX_MESSAGES - 1 : -1])
    return StreamingResponse.from_chain(chain, request.messages[-1].content)


app = FastAPI()
app.include_router(router, tags=["chat"])

origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
