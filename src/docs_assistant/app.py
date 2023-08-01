#!/usr/bin/env python3
import logging
import weaviate
from enum import StrEnum

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from lanarky.responses import StreamingResponse
from lanarky.routing import LangchainRouter, LLMCacheMode
from pydantic import BaseModel, constr
import langchain
from langchain.agents import initialize_agent, Tool, AgentType
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores.weaviate import Weaviate
from langchain.vectorstores import FAISS
from langchain.chains import RetrievalQA, RetrievalQAWithSourcesChain
from langchain.chains.qa_with_sources.loading import load_qa_with_sources_chain
from langchain.prompts import PromptTemplate

from config import *
from prompts import *

# for caching
from gptcache import Cache
from gptcache.adapter.api import init_similar_cache
from langchain.cache import GPTCache
import hashlib

def get_hashed_name(name):
    return hashlib.sha256(name.encode()).hexdigest()


def init_gptcache(cache_obj: Cache, llm: str):
    hashed_llm = get_hashed_name(llm)
    init_similar_cache(cache_obj=cache_obj, data_dir=f"similar_cache_{hashed_llm}")

langchain.llm_cache = GPTCache(init_gptcache)

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
    model: str,
    messages: list[Message],
    max_tokens: int,
    temperature: float,
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
    
    system_prompt = next(
        (message.content for message in messages if message.role == Role.SYSTEM),
        QA_DOCS_ASSISTANT,
    )
    chat_prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessagePromptTemplate.from_template(system_prompt),
            MessagesPlaceholder(variable_name="history"),
            HumanMessagePromptTemplate.from_template("{query}"),
        ]
    )
    print(chat_prompt)

    chat_memory = ChatMessageHistory()
    for message in messages:
        if message.role == Role.USER:
            chat_memory.add_user_message(message.content)
        elif message.role == Role.ASSISTANT:
            chat_memory.add_ai_message(message.content)

    memory = ConversationBufferMemory(
        chat_memory=chat_memory, return_messages=True
    )
    print(chat_memory)

    oai_embeddings = OpenAIEmbeddings(openai_api_key=settings.OPENAI_API_KEY)
    llm = ChatOpenAI(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        openai_api_key=settings.OPENAI_API_KEY,
        streaming=True,
        verbose=True,
    )

    # connect to weaviate instance
    client = weaviate.Client(url=settings.WEAVIATE_URL)
    attributes = ['header', 'source']
    weaviate_docs = Weaviate(client, 
        settings.DOCS_INDEX, # capitalized
        "text", # constant
        embedding=oai_embeddings,
        attributes=attributes,
        by_text=False # force vector search
    )

    qa_prompt_docs = PromptTemplate.from_template(QA_DOCS_ASSISTANT)
    qa_chain_docs = load_qa_with_sources_chain(
        llm = llm,
        chain_type = "stuff",
        prompt=qa_prompt_docs, # for stuff
        # promp = chat_prompt,
        # memory = memory,
        verbose = True
    )
    retrival_qa_chain_docs = RetrievalQAWithSourcesChain(
        combine_documents_chain=qa_chain_docs, 
        retriever=weaviate_docs.as_retriever(),
    )
    
    return retrival_qa_chain_docs


router = LangchainRouter(llm_cache_mode=LLMCacheMode.IN_MEMORY)


@router.post(
    "/chat",
    summary="Langchain Chat",
    description="Chat with OpenAI's chat models using Langchain",
)
def chat(request: ChatRequest):
    chain = create_chain(
        model=request.model,
        messages=request.messages[:-1],
        max_tokens=request.max_tokens,
        temperature=request.temperature
    )
    # print(request.messages)
    return StreamingResponse.from_chain(chain, request.messages[-1].content)


app = FastAPI()
app.include_router(router, tags=["chat"])
