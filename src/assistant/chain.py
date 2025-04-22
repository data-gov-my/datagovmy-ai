import json
import os
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from langchain_openai.chat_models import ChatOpenAI
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_core.prompts.chat import (
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
    RunnablePassthrough,
    RunnableParallel,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_chroma import Chroma

from operator import itemgetter

from prompts import (
    QA_DOCS_PREFIX,
    QA_SUFFIX_NEW,
    QUERY_EXPAND_PROMPT,
    QUERY_REWRITE_PROMPT,
)
from schema import *
from utils.templates import CATALOGUE_ID_TEMPLATE, CATALOGUE_ID_NOAPI_TEMPLATE


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
            if dc_meta["exclude_openapi"]:
                template = CATALOGUE_ID_NOAPI_TEMPLATE
            else:
                template = CATALOGUE_ID_TEMPLATE
            page_content = template.format(
                subcategory=dc_meta["subcategory"],
                category=dc_meta["category"],
                id=dc_meta["id"],
                description=dc_meta["description"],
                data_methodology=dc_meta["data_methodology"],
                update_frequency=dc_meta["update_frequency"],
                # data_source=dc_meta["data_source"],
                data_caveat=dc_meta["data_caveat"],
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
    embedding_llm = OpenAIEmbeddings(model="text-embedding-3-small")

    llm = ChatOpenAI(
        model="gpt-4.1",
        temperature=0,
        streaming=True,
        verbose=True,
    )

    # init retriever
    client = chromadb.HttpClient(
        host=os.getenv("CHROMA_HOST"),
        port=os.getenv("CHROMA_PORT"),
        settings=Settings(),
    )

    db = Chroma(
        client=client,
        collection_name="dgmy_docs",
        embedding_function=embedding_llm,
    )

    custom_docs_retriever = DocsRetriever(vectorstore=db)

    query_expand_prompt = ChatPromptTemplate.from_template(QUERY_EXPAND_PROMPT)

    generate_queries = (
        RunnablePassthrough()
        | query_expand_prompt
        | ChatOpenAI(model="gpt-4.1-mini", temperature=0.7)
        | StrOutputParser()
        | (lambda x: x.split("\n"))
        | (lambda x: [q for q in x if q.strip()])  # remove empty strings
    ).with_config({"run_name": "QueryExpand"})

    ensemble_retriever_obj = EnsembleRetriever(
        retrievers=[custom_docs_retriever], weights=[0.33, 0.33, 0.33]
    )

    def apply_rrf(docs: List[List[Document]]):
        ranked_docs = ensemble_retriever_obj.weighted_reciprocal_rank(docs)
        return "\n\n".join([doc.page_content for doc in ranked_docs])

    retrieval_chain = (
        {"query": RunnablePassthrough()}
        | generate_queries
        | RunnableParallel(
            {
                "queries": RunnablePassthrough(),
                "context": custom_docs_retriever.map() | apply_rrf,
            }
        )
    ).with_config({"run_name": "RetrievalChain"})

    multi_query_retriever_chain = (
        itemgetter("query") | retrieval_chain.pick(["context"]) | itemgetter("context")
    ).with_config({"run_name": "MultiQueryRetriever"})

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

    # query rewriting to handle low-context questions
    query_rewrite_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", QUERY_REWRITE_PROMPT),
            MessagesPlaceholder(variable_name="history"),
            ("user", "User question: {query}"),
        ]
    )
    query_rewrite_chain = (
        query_rewrite_prompt
        | ChatOpenAI(model="gpt-4.1-mini", temperature=0.7)
        | StrOutputParser()
    ).with_config({"run_name": "QueryRewrite"})

    rag_chain = (
        format_messages
        | RunnableParallel(
            {
                "context": multi_query_retriever_chain,
                "query": query_rewrite_chain,
                "history": itemgetter("history"),
            }
        )
        | qa_chain
    ).with_config({"run_name": "RAGChain"})

    return rag_chain
