import json
from typing import Any, Coroutine, Dict, List, Optional
from langchain.callbacks.manager import Callbacks
from langchain.schema.document import Document
import weaviate
from langchain.chains import RetrievalQAWithSourcesChain
from langchain.schema import BaseRetriever
from langchain.docstore.document import Document
from langchain.vectorstores import VectorStore

from prompts import *
from schema import *
from config import *
from utils.templates import CATALOGUE_ID_TEMPLATE


class DocsRetriever(BaseRetriever, BaseModel):
    vectorstore: VectorStore

    class Config:
        arbitrary_types_allowed = True

    def get_page_content(self, doc) -> str:
        """Create page_content to embed into context"""
        metadata = doc.metadata
        # if dc_meta source, inject catalogue name and id into open API guide
        if "dc_meta" in doc.metadata["source"]:
            dc_meta = json.loads(metadata["header"])
            page_content = CATALOGUE_ID_TEMPLATE.format(
                subcategory=dc_meta["subcategory"],
                category=dc_meta["category"],
                id=dc_meta["id"],
                description=dc_meta["description"],
                data_methodology=dc_meta["data_methodology"],
                update_frequency=dc_meta["update_frequency"],
                data_sources=dc_meta["data_sources"],
                data_caveat=dc_meta["data_caveat"],
                dc_page_id=dc_meta["dc_page_id"],
            )
            return page_content
        else:
            return doc.page_content

    def get_relevant_documents(self, query):
        docs = []
        for doc in self.vectorstore.max_marginal_relevance_search(query, k=4):
            content = self.get_page_content(doc)
            docs.append(Document(page_content=content, metadata=doc.metadata))

        return docs

    async def _aget_relevant_documents(self, query) -> List[Document]:
        docs_similar = await self.vectorstore.amax_marginal_relevance_search(query, k=4)
        docs = []
        for doc in docs_similar:
            content = self.get_page_content(doc)
            docs.append(Document(page_content=content, metadata=doc.metadata))
        return docs


def create_chain(
    chain_type: ChainType,
    messages: list[Message],
) -> RetrievalQAWithSourcesChain:
    from langchain.chat_models import ChatOpenAI
    from langchain.embeddings.openai import OpenAIEmbeddings
    from langchain.memory import ConversationBufferMemory
    from langchain.memory.chat_message_histories.in_memory import ChatMessageHistory
    from langchain.chains import RetrievalQA, RetrievalQAWithSourcesChain
    from langchain.vectorstores.weaviate import Weaviate
    from langchain.chains.qa_with_sources.loading import load_qa_with_sources_chain
    from langchain.prompts.chat import (
        ChatPromptTemplate,
        HumanMessagePromptTemplate,
        MessagesPlaceholder,
        SystemMessagePromptTemplate,
    )

    chain_config = {
        ChainType.DOCS: {
            "system_message": QA_DOCS_PREFIX,
            "vector_index": settings.DOCS_VINDEX,
            "vector_attr": ["header", "source"],
        },
        ChainType.MAIN: {
            "system_message": QA_DATA_PREFIX,
            "vector_index": settings.DC_META_VINDEX,  # to replace with DATA_VINDEX
            "vector_attr": ["name", "description", "category", "agency", "source"],
        },
    }

    chat_prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessagePromptTemplate.from_template(
                chain_config[chain_type]["system_message"] + QA_SUFFIX
            ),
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
        max_tokens=2500,
        temperature=0,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_organization=settings.OPENAI_ORG_ID,
        streaming=True,
        verbose=True,
    )

    # connect to weaviate instance
    client = weaviate.Client(url=settings.WEAVIATE_URL)
    attributes = chain_config[chain_type]["vector_attr"]
    weaviate_docs = Weaviate(
        client,
        chain_config[chain_type]["vector_index"],
        "text",  # constant
        embedding=embedding_llm,
        attributes=attributes,
        by_text=False,  # force vector search
    )

    custom_docs_retriever = DocsRetriever(vectorstore=weaviate_docs)

    qa_chain_docs = load_qa_with_sources_chain(
        llm=llm, chain_type="stuff", prompt=chat_prompt, memory=memory, verbose=True
    )
    retrival_qa_chain_docs = RetrievalQAWithSourcesChain(
        combine_documents_chain=qa_chain_docs,
        # retriever=weaviate_docs.as_retriever(search_kwargs={"k": 5}),
        retriever=custom_docs_retriever,
        question_key="query",
    )

    return retrival_qa_chain_docs
