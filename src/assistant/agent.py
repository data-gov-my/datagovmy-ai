import json
import os
from typing import Any, Dict, List, Optional, TypedDict
from typing_extensions import Annotated

import chromadb
from chromadb.config import Settings
from langchain_openai.chat_models import ChatOpenAI
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain_core.prompts.chat import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_classic.retrievers import EnsembleRetriever
from langchain_core.output_parsers import StrOutputParser
from langchain_core.vectorstores import VectorStore
from langchain_core.messages import AIMessage, HumanMessage, BaseMessage
from langchain_chroma import Chroma
from langgraph.graph import StateGraph, START, END, MessagesState

from prompts import (
    QA_DOCS_PREFIX,
    QA_SUFFIX_NEW,
    QUERY_EXPAND_PROMPT,
    QUERY_REWRITE_PROMPT,
)
from schema import Role
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


class RAGState(MessagesState):
    """State for the RAG agent graph"""

    history: Optional[List[BaseMessage]]
    query: Optional[str]
    expanded_queries: Optional[List[str]]
    context: Optional[str]
    rewritten_query: Optional[str]
    answer: Optional[str]


def apply_rrf(
    docs: List[List[Document]], ensemble_retriever_obj: EnsembleRetriever
) -> str:
    """Apply Reciprocal Rank Fusion to merge multiple document lists"""
    num_expected_lists = len(ensemble_retriever_obj.weights)

    processed_docs = list(docs)
    current_num_lists = len(processed_docs)

    if current_num_lists < num_expected_lists:
        # pad with empty lists if fewer lists are provided than expected
        processed_docs.extend(
            [[] for _ in range(num_expected_lists - current_num_lists)]
        )
    elif current_num_lists > num_expected_lists:
        # truncate if more lists are provided than expected
        processed_docs = processed_docs[:num_expected_lists]

    # if all document lists are empty, return an empty string
    if not processed_docs or not any(processed_docs):
        return ""

    ranked_docs = ensemble_retriever_obj.weighted_reciprocal_rank(processed_docs)
    return "\n\n".join([doc.page_content for doc in ranked_docs])


def create_rag_agent():
    """Create a LangGraph agent that replicates the chain implementation"""
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

    ensemble_retriever_obj = EnsembleRetriever(
        retrievers=[custom_docs_retriever], weights=[0.33, 0.33, 0.33]
    )

    query_expand_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.7)
    query_rewrite_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.7)

    # Node 1: Format messages - extract history and query from input
    def format_messages_node(state: RAGState) -> Dict[str, Any]:
        """Extract history and query from messages"""
        messages = state.get("messages", [])
        base_messages = []
        query = ""

        # Convert messages to BaseMessage objects if they're dicts
        converted_messages = []
        for msg in messages:
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == Role.USER:
                    converted_messages.append(HumanMessage(content=content))
                elif role == Role.ASSISTANT:
                    converted_messages.append(AIMessage(content=content))
            else:
                converted_messages.append(msg)

        # Extract history (all but last message) and query (last message)
        if converted_messages:
            base_messages = converted_messages[:-1]
            last_message = converted_messages[-1]
            if isinstance(last_message, HumanMessage):
                query = last_message.content
            else:
                query = str(getattr(last_message, "content", ""))

        return {
            "history": base_messages,
            "query": query,
        }

    # Node 2: Expand queries - generate multiple query variations
    def expand_queries_node(state: RAGState) -> Dict[str, Any]:
        """Expand a single query into multiple queries"""
        query = state.get("query", "")

        if not query:
            return {"expanded_queries": []}

        # Dynamically construct prompt based on current query
        query_expand_prompt = ChatPromptTemplate.from_template(QUERY_EXPAND_PROMPT)

        # Generate expanded queries
        expanded_queries_str = (
            query_expand_prompt | query_expand_llm | StrOutputParser()
        ).invoke({"query": query})

        # Split by newlines and clean up
        expanded_queries = [
            q.strip() for q in expanded_queries_str.split("\n") if q.strip()
        ]

        # Fallback to original query if expansion failed
        if not expanded_queries:
            expanded_queries = [query]

        return {"expanded_queries": expanded_queries}

    # Node 3: Retrieve documents - retrieve docs for each expanded query
    def retrieve_documents_node(state: RAGState) -> Dict[str, Any]:
        """Retrieve documents using expanded queries"""
        expanded_queries = state.get("expanded_queries", [])
        query = state.get("query", "")

        # Fallback to original query if no expanded queries
        if not expanded_queries:
            expanded_queries = [query] if query else []

        if not expanded_queries:
            return {"context": ""}

        # Retrieve documents for each query
        all_docs = []
        for q in expanded_queries:
            docs = custom_docs_retriever.get_relevant_documents(q)
            all_docs.append(docs)

        # Apply RRF to merge results
        context = apply_rrf(all_docs, ensemble_retriever_obj)

        return {"context": context}

    # Node 4: Rewrite query - rewrite vague queries
    def rewrite_query_node(state: RAGState) -> Dict[str, Any]:
        """Rewrite the query if needed"""
        query = state.get("query", "")
        history = state.get("history", [])

        if not query:
            return {"rewritten_query": ""}

        # Dynamically construct prompt based on current state (query and history)
        query_rewrite_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", QUERY_REWRITE_PROMPT),
                MessagesPlaceholder(variable_name="history"),
                ("user", "User question: {query}"),
            ]
        )

        # Rewrite query
        rewritten_query = (
            query_rewrite_prompt | query_rewrite_llm | StrOutputParser()
        ).invoke({"query": query, "history": history})

        return {"rewritten_query": rewritten_query}

    # Node 5: Generate answer - generate final answer
    def generate_answer_node(state: RAGState) -> Dict[str, Any]:
        """Generate the final answer"""
        context = state.get("context", "")
        rewritten_query = state.get("rewritten_query", state.get("query", ""))
        history = state.get("history", [])

        # Dynamically construct prompt based on current state (context, query, history)
        # The context is dynamically retrieved, so the prompt must be constructed at runtime
        chat_prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessagePromptTemplate.from_template(
                    QA_DOCS_PREFIX + QA_SUFFIX_NEW
                ),
                MessagesPlaceholder(variable_name="history"),
                HumanMessagePromptTemplate.from_template("{query}"),
            ]
        )

        # Generate answer
        answer = (chat_prompt | llm | StrOutputParser()).invoke(
            {
                "context": context,
                "query": rewritten_query,
                "history": history,
            }
        )

        # Add answer as AIMessage to messages for LangServe compatibility
        # MessagesState will automatically merge this with existing messages
        return {
            "answer": answer,
            "messages": [AIMessage(content=answer)],
        }

    # Build the graph
    workflow = StateGraph(RAGState)

    # Add nodes
    workflow.add_node("format_messages", format_messages_node)
    workflow.add_node("expand_queries", expand_queries_node)
    workflow.add_node("retrieve_documents", retrieve_documents_node)
    workflow.add_node("rewrite_query", rewrite_query_node)
    workflow.add_node("generate_answer", generate_answer_node)

    # Define edges
    # Start -> format_messages
    workflow.add_edge(START, "format_messages")

    # After formatting, run expand_queries and rewrite_query in parallel
    # Both can run independently since they only need query and history
    workflow.add_edge("format_messages", "expand_queries")
    workflow.add_edge("format_messages", "rewrite_query")

    # Retrieve documents after queries are expanded (depends on expand_queries)
    workflow.add_edge("expand_queries", "retrieve_documents")

    # Generate answer after both retrieval and query rewrite are done
    # LangGraph will wait for both nodes to complete before executing generate_answer
    workflow.add_edge("retrieve_documents", "generate_answer")
    workflow.add_edge("rewrite_query", "generate_answer")

    # End after generating answer
    workflow.add_edge("generate_answer", END)

    # Compile the graph
    graph = workflow.compile()

    return graph
