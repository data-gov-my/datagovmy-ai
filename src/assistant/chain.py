import json
from typing import Any, Coroutine, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from langchain_openai.chat_models import ChatOpenAI
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain.memory.chat_message_histories.in_memory import ChatMessageHistory
from langchain.prompts.chat import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain.docstore.document import Document
from langchain_core.retrievers import BaseRetriever
from langchain.retrievers import EnsembleRetriever
from langchain_core.output_parsers import StrOutputParser
from langchain_core.vectorstores import VectorStore
from langchain_core.runnables import (
    RunnableLambda,
    ConfigurableFieldSpec,
    RunnablePassthrough,
)
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langchain_chroma import Chroma

from operator import itemgetter

from prompts import QA_DOCS_PREFIX, QA_SUFFIX_NEW, QUERY_EXPAND_PROMPT
from schema import *
from config import *
from utils.templates import CATALOGUE_ID_TEMPLATE


class DocsRetriever(BaseRetriever):
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
                # data_source=dc_meta["data_source"],
                data_caveat=dc_meta["data_caveat"],
                dc_page_id=dc_meta["id"],
            )
            return page_content
        else:
            return doc.page_content + "\n\nSource: " + metadata["source"]

    def _get_relevant_documents(self, query):
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


def create_new_chain():
    embedding_llm = OpenAIEmbeddings()

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        streaming=True,
        verbose=True,
    )

    # init retriever
    client = chromadb.HttpClient(settings=Settings(allow_reset=True))

    db = Chroma(
        client=client,
        collection_name="dgmy_docs",
        embedding_function=embedding_llm,
    )

    custom_docs_retriever = DocsRetriever(vectorstore=db)

    prompt = ChatPromptTemplate.from_template(QUERY_EXPAND_PROMPT)

    generate_queries = (
        {"question": RunnablePassthrough()}
        | prompt
        | ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
        | StrOutputParser()
        | (lambda x: x.split("\n"))
        | (lambda x: [q for q in x if q.strip()])  # remove empty strings
    )

    ensemble_retriever_obj = EnsembleRetriever(
        retrievers=[custom_docs_retriever], weights=[0.33, 0.33, 0.33]
    )

    def apply_rrf(docs: List[List[Document]]):
        ranked_docs = ensemble_retriever_obj.weighted_reciprocal_rank(docs)
        return "\n\n".join([doc.page_content for doc in ranked_docs])

    retrieval_chain = (
        {"query": RunnablePassthrough()}
        | generate_queries
        | custom_docs_retriever.map()
        | apply_rrf
    )

    multi_query_retriever_chain = itemgetter("query") | retrieval_chain

    # handle chat history - received from UI in list of messages
    def format_messages(input):
        messages = input["messages"]
        base_messages = []
        for message in messages[:-1]:
            if message["role"] == Role.USER:
                base_messages.append(HumanMessage(message["content"]))
            elif message["role"] == Role.ASSISTANT:
                base_messages.append(AIMessage(message["content"]))
        return {"history": base_messages, "query": messages[-1]["content"]}

    chat_prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessagePromptTemplate.from_template(QA_DOCS_PREFIX + QA_SUFFIX_NEW),
            MessagesPlaceholder(variable_name="history"),
            HumanMessagePromptTemplate.from_template("{query}"),
        ]
    )

    qa_chain = chat_prompt | llm | StrOutputParser()

    rag_chain = (
        RunnableLambda(format_messages)
        | RunnablePassthrough.assign(context=multi_query_retriever_chain)
        | qa_chain
    )

    return rag_chain
