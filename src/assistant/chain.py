"""Docs assistant RAG graph.

START -> analyse --(docs)--> retrieve (one Send per query) -> fuse -> generate -> END
                 +--(smalltalk)------------------------------------> generate
                 +--(blocked / off-topic)--------------------------> decline -> END
                 +--(unclear / too long / failure)-----------------> refuse -> END

`analyse` is the input filter: one structured call classifies the latest
message (jailbreaks, impersonation and political requests are blocked) and
rewrites it into a sanitised standalone question with the assumed context
added. The answer model only ever sees that rewrite plus retrieved docs, never
raw user text. Declines are written by a model that sees only the decline
reason, a sanitised topic label and the language.
"""

import asyncio
import json
import logging
import operator
import os
import re
from typing import Annotated, Any, AsyncIterator, Awaitable, Callable, List, Optional

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    convert_to_messages,
    trim_messages,
)
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import (
    Runnable,
    RunnableConfig,
    RunnableGenerator,
    RunnableLambda,
)
from langgraph.cache.base import BaseCache
from langgraph.cache.memory import InMemoryCache
from langgraph.errors import NodeError
from langgraph.graph import START, END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import CachePolicy, Command, RetryPolicy, Send, TimeoutPolicy
from typing_extensions import TypedDict

from guards import AnswerGuard
from models import get_answer_llm, get_fast_llm, get_vectorstore
from prompts import (
    CANNED_REPLIES,
    DECLINE_PROMPT,
    DECLINE_REASONS,
    DECLINE_USER_TEMPLATE,
    QA_DOCS_PREFIX,
    QA_SUFFIX_NEW,
    QUERY_ANALYSIS_PROMPT,
)
from schema import DeclineReason, Intent, QueryAnalysis, Role
from utils.templates import CATALOGUE_ID_TEMPLATE, CATALOGUE_ID_NOAPI_TEMPLATE

logger = logging.getLogger(__name__)

# maximum history of messages for memory
MAX_MESSAGES = 5
DOCS_PER_QUERY = 4
MAX_CONTEXT_DOCS = int(os.getenv("RAG_MAX_CONTEXT_DOCS") or 8)
RETRIEVE_TIMEOUT_SECONDS = 20
# longer messages are refused without calling a model: long prompts are the
# usual many-shot jailbreak payload and make the classifier less reliable, while
# leaving room for pasted code, errors and API responses
MAX_QUESTION_CHARS = int(os.getenv("RAG_MAX_QUESTION_CHARS") or 4000)
TOPIC_MAX_WORDS = 6
# query analysis cache ttl
CACHE_TTL_SECONDS = int(os.getenv("RAG_CACHE_TTL_SECONDS") or 900)
RRF_K = 60

DOCS_BASE_URL = "https://developer.data.gov.my"
CATALOGUE_BASE_URL = "https://data.gov.my/data-catalogue"
# docs sources that are sections of other pages, not pages of their own
DOCS_WITHOUT_PAGE = {"sample-query", "authentication"}


# --- retrieval helpers ---


def format_doc(doc: Document) -> str:
    """Create page_content to embed into context"""
    metadata = doc.metadata
    # if dc_meta source, inject catalogue name and id into open API guide
    if "dc_meta" in metadata["source"]:
        dc_meta = json.loads(metadata["header"])
        if dc_meta["exclude_openapi"]:
            template = CATALOGUE_ID_NOAPI_TEMPLATE
        else:
            template = CATALOGUE_ID_TEMPLATE
        return template.format(
            subcategory=dc_meta["subcategory"],
            category=dc_meta["category"],
            id=dc_meta["id"],
            description=dc_meta["description"],
            data_methodology=dc_meta["data_methodology"],
            update_frequency=dc_meta["update_frequency"],
            data_caveat=dc_meta["data_caveat"],
        )
    return doc.page_content + "\n\nSource: " + metadata["source"]


def source_info(doc: Document) -> dict:
    """Best-effort link for a retrieved chunk, for display as a citation"""
    source = doc.metadata.get("source", "")
    if "dc_meta" in source:
        dc_meta = json.loads(doc.metadata["header"])
        return {
            "title": f"{dc_meta['subcategory']} {dc_meta['category']}".strip(),
            "url": f"{CATALOGUE_BASE_URL}/{dc_meta['id']}",
        }
    # docs mdx path relative to pages/, e.g. realtime-api/weather (ingest strips
    # the .en.mdx suffix)
    route = source.removesuffix(".en.mdx").removesuffix("/index")
    if source and route not in DOCS_WITHOUT_PAGE:
        # top-level index is the docs home page
        path = "" if route == "index" else f"/{route}"
        return {
            "title": doc.metadata.get("header", route),
            "url": f"{DOCS_BASE_URL}{path}",
        }
    return {"title": doc.metadata.get("header", source), "url": None}


def reciprocal_rank_fusion(
    doc_lists: List[List[Document]], k: int = RRF_K
) -> List[Document]:
    """Fuse ranked lists into one, deduplicating on page_content"""
    scores: dict[str, float] = {}
    docs: dict[str, Document] = {}
    for doc_list in doc_lists:
        for rank, doc in enumerate(doc_list):
            key = doc.page_content
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            docs.setdefault(key, doc)
    return [docs[key] for key in sorted(scores, key=scores.get, reverse=True)]


async def search_docs(query: str) -> List[Document]:
    docs = await get_vectorstore().amax_marginal_relevance_search(
        query, k=DOCS_PER_QUERY
    )
    return [
        Document(id=doc.id, page_content=format_doc(doc), metadata=doc.metadata)
        for doc in docs
    ]


# --- graph ---


class RAGInput(TypedDict):
    messages: List[AnyMessage]


class RAGOutput(TypedDict):
    answer: str
    context: List[Document]


class RAGState(RAGInput, RAGOutput, total=False):
    # plain values only: node cache entries must be serialisable
    intent: str  # Intent, or "too_long" / "unavailable"
    language: Optional[str]
    decline_reason: Optional[str]
    topic: str
    standalone_question: str
    queries: List[str]
    retrieved: Annotated[List[List[Document]], operator.add]


class RetrieveInput(TypedDict):
    query: str


def latest_question(state: RAGState) -> str:
    return state["messages"][-1].text


def build_queries(question: str, analysis: QueryAnalysis) -> List[str]:
    """Original question + standalone rewrite + expansions, deduplicated"""
    if analysis.intent != Intent.DOCS:
        return []
    queries, seen = [], set()
    for query in [question, analysis.standalone_question, *analysis.search_queries]:
        query = query.strip()
        if query and query.lower() not in seen:
            seen.add(query.lower())
            queries.append(query)
    return queries


def sanitise_topic(topic: str) -> str:
    """Letters, digits, spaces and hyphens only, at most TOPIC_MAX_WORDS words:
    the label reaches the decline model, so it must not carry statements"""
    words = re.sub(r"[^\w\s-]", " ", topic).split()
    return " ".join(words[:TOPIC_MAX_WORDS])


def canned_reply(kind: str, language: Optional[str]) -> str:
    replies = CANNED_REPLIES[kind]
    if language in replies:
        return replies[language]
    # language unknown when the message never reached the analyser
    return replies["en"] + "\n\n" + replies["ms"]


def build_analyser() -> Runnable:
    """{"messages": [...]} -> QueryAnalysis, also used by the eval runner"""
    analysis_prompt = ChatPromptTemplate.from_messages(
        [("system", QUERY_ANALYSIS_PROMPT), MessagesPlaceholder("messages")]
    )
    return analysis_prompt | get_fast_llm().with_structured_output(QueryAnalysis)


def build_rag_graph(
    analyser: Optional[Runnable] = None,
    answer_llm: Optional[BaseChatModel] = None,
    decline_llm: Optional[BaseChatModel] = None,
    search: Callable[[str], Awaitable[List[Document]]] = search_docs,
    cache: Optional[BaseCache] = None,
) -> CompiledStateGraph:
    analyser = (analyser or build_analyser()).with_config({"run_name": "QueryAnalysis"})

    qa_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", QA_DOCS_PREFIX + QA_SUFFIX_NEW),
            MessagesPlaceholder("messages"),
        ]
    )
    qa_chain = (
        qa_prompt | (answer_llm or get_answer_llm()) | StrOutputParser()
    ).with_config({"run_name": "Answer"})

    decline_prompt = ChatPromptTemplate.from_messages(
        [("system", DECLINE_PROMPT), ("human", DECLINE_USER_TEMPLATE)]
    )
    decline_chain = (
        decline_prompt | (decline_llm or get_fast_llm()) | StrOutputParser()
    ).with_config({"run_name": "Decline"})

    async def analyse(state: RAGState):
        if len(latest_question(state)) > MAX_QUESTION_CHARS:
            return {"intent": "too_long", "language": None}
        analysis = await analyser.ainvoke({"messages": state["messages"]})
        return {
            "intent": analysis.intent.value,
            "language": analysis.language,
            "standalone_question": analysis.standalone_question.strip(),
            "queries": build_queries(latest_question(state), analysis),
            "decline_reason": analysis.decline_reason and analysis.decline_reason.value,
            # the label is only needed (and only passed on) for unrelated requests
            "topic": (
                sanitise_topic(analysis.topic)
                if analysis.intent == Intent.OFF_TOPIC
                else ""
            ),
        }

    def analyse_fallback(state: RAGState, error: NodeError):
        # fail closed: without the filter, raw input would reach the answer
        # model, and a message crafted to break the analyser must not bypass it
        logger.warning("Query analysis failed: %r", error.error)
        # error handlers replace the failed node's edges, so route explicitly
        return Command(
            update={"intent": "unavailable", "language": None}, goto="refuse"
        )

    def route(state: RAGState):
        if state["intent"] in (Intent.BLOCKED, Intent.OFF_TOPIC):
            return "decline"
        # the answer model needs a sanitised rewrite, refuse if there is none
        if state["intent"] not in (Intent.DOCS, Intent.SMALLTALK) or not state.get(
            "standalone_question"
        ):
            return "refuse"
        if state["intent"] == Intent.DOCS and state.get("queries"):
            return [Send("retrieve", {"query": query}) for query in state["queries"]]
        return "generate"

    async def decline(state: RAGState):
        # sees only the reason, a sanitised topic label and the language,
        # never user text, so the reply can be contextual but not steered
        intent = state["intent"]
        reason = state.get("decline_reason")
        if reason not in DECLINE_REASONS:
            reason = (
                DeclineReason.UNRELATED_TOPIC
                if intent == Intent.OFF_TOPIC
                else DeclineReason.HARMFUL
            )
        topic = state.get("topic", "") if intent == Intent.OFF_TOPIC else ""
        try:
            answer = await decline_chain.ainvoke(
                {
                    "reason": DECLINE_REASONS[reason],
                    "topic": topic or "(none)",
                    "language": "Malay" if state.get("language") == "ms" else "English",
                }
            )
        except Exception as e:
            logger.warning("Decline reply failed, using canned reply: %r", e)
            answer = ""
        if not answer.strip():
            answer = canned_reply(intent, state.get("language"))
        return {"answer": answer, "context": []}

    def refuse(state: RAGState):
        kind = state["intent"]
        if kind not in CANNED_REPLIES:
            kind = "blocked"  # e.g. docs/smalltalk with an empty rewrite
        # unclear messages are often a single word or random characters, too
        # short to detect the language reliably, so reply in both
        language = None if kind == Intent.UNCLEAR else state.get("language")
        return {"answer": canned_reply(kind, language), "context": []}

    search_with_retry = RunnableLambda(search, name="DocsSearch").with_retry(
        stop_after_attempt=3
    )

    async def retrieve(state: RetrieveInput):
        # retries and fallback are handled here rather than with node policies:
        # langgraph re-raises errors from fanned-out (Send) siblings even when
        # an error_handler is set, and one failed query shouldn't fail the answer
        try:
            docs = await asyncio.wait_for(
                search_with_retry.ainvoke(state["query"]), RETRIEVE_TIMEOUT_SECONDS
            )
        except Exception as e:
            logger.warning("Retrieval failed for %r: %r", state["query"], e)
            docs = []
        return {"retrieved": [docs]}

    def fuse(state: RAGState):
        fused = reciprocal_rank_fusion(state.get("retrieved", []))
        return {"context": fused[:MAX_CONTEXT_DOCS]}

    async def generate(state: RAGState):
        context = "\n\n".join(doc.page_content for doc in state.get("context", []))
        # only the sanitised rewrite: raw user turns (including earlier ones that
        # may carry injected instructions) never reach the answer model
        answer = await qa_chain.ainvoke(
            {
                "context": context,
                "messages": [HumanMessage(state["standalone_question"])],
            }
        )
        return {"answer": answer}

    graph_builder = StateGraph(RAGState, input_schema=RAGInput, output_schema=RAGOutput)
    graph_builder.add_node(
        "analyse",
        analyse,
        retry_policy=RetryPolicy(max_attempts=2),
        timeout=TimeoutPolicy(run_timeout=30),
        cache_policy=CachePolicy(ttl=CACHE_TTL_SECONDS),
        error_handler=analyse_fallback,
    )
    graph_builder.add_node(
        "retrieve",
        retrieve,
        input_schema=RetrieveInput,
    )
    graph_builder.add_node("fuse", fuse)
    graph_builder.add_node("decline", decline, timeout=TimeoutPolicy(idle_timeout=30))
    graph_builder.add_node("refuse", refuse)
    # no graph-level retry on generate: a retry after tokens were streamed would
    # duplicate output, the OpenAI client already retries failed requests
    graph_builder.add_node("generate", generate, timeout=TimeoutPolicy(idle_timeout=60))
    graph_builder.add_edge(START, "analyse")
    graph_builder.add_conditional_edges(
        "analyse", route, ["retrieve", "generate", "decline", "refuse"]
    )
    graph_builder.add_edge("retrieve", "fuse")
    graph_builder.add_edge("fuse", "generate")
    graph_builder.add_edge("generate", END)
    graph_builder.add_edge("decline", END)
    graph_builder.add_edge("refuse", END)
    return graph_builder.compile(cache=cache or InMemoryCache(), name="DocsRAG")


# --- serving adapters ---


def to_graph_input(payload: dict) -> RAGInput:
    """Convert chat history received from UI (list of role/content dicts)"""
    turns = [
        (message["role"], message["content"])
        for message in payload["messages"]
        if message["role"] in (Role.USER, Role.ASSISTANT)
    ]
    # earlier turns (client-supplied, so assistant turns can be forged too) are
    # truncated rather than refused; the latest message is checked in analyse
    turns = [
        (role, content[:MAX_QUESTION_CHARS]) for role, content in turns[:-1]
    ] + turns[-1:]
    messages = convert_to_messages(turns)
    messages = trim_messages(
        messages,
        strategy="last",
        token_counter=len,
        max_tokens=MAX_MESSAGES,
        start_on="human",
    )
    return {"messages": messages}


async def stream_chat_events(
    graph: CompiledStateGraph, payload: dict, config: Optional[RunnableConfig] = None
) -> AsyncIterator[dict[str, Any]]:
    """Yield {"type": "sources"|"token", "data": ...} events for a chat request.

    Generated answers pass through AnswerGuard (link allowlist, echo check)
    before they are released.
    """
    graph_input = to_graph_input(payload)
    guard = AnswerGuard([m.text for m in graph_input["messages"] if m.type == "human"])
    streamed, language = False, None
    async for mode, chunk in graph.astream(
        graph_input, config, stream_mode=["updates", "messages"]
    ):
        if mode == "updates" and "analyse" in chunk:
            language = (chunk["analyse"] or {}).get("language")
        elif mode == "updates" and "fuse" in chunk:
            context = chunk["fuse"]["context"]
            guard.set_context("\n\n".join(doc.page_content for doc in context))
            yield {"type": "sources", "data": [source_info(doc) for doc in context]}
        elif (
            mode == "updates" and not streamed and chunk.keys() & {"refuse", "decline"}
        ):
            # canned replies (incl. a failed decline) aren't model output,
            # emit them as a single token
            node = "refuse" if "refuse" in chunk else "decline"
            yield {"type": "token", "data": chunk[node]["answer"]}
        elif mode == "messages":
            message, metadata = chunk
            if (
                metadata.get("langgraph_node") in ("generate", "decline")
                and message.text
            ):
                streamed = True
                if text := guard.feed(message.text):
                    yield {"type": "token", "data": text}
                if guard.blocked:
                    break
    if text := guard.finish():
        yield {"type": "token", "data": text}
    if guard.blocked:
        yield {"type": "token", "data": "\n\n" + canned_reply("blocked", language)}


def create_chat_runnable(graph: Optional[CompiledStateGraph] = None) -> Runnable:
    """LangServe-compatible runnable: takes {"messages": [...]} and streams the
    answer as strings, matching the contract of the previous LCEL chain"""
    graph = graph or build_rag_graph()

    async def stream_answer(
        inputs: AsyncIterator[dict], config: RunnableConfig
    ) -> AsyncIterator[str]:
        payload = {}
        async for chunk in inputs:
            payload = chunk
        async for event in stream_chat_events(graph, payload, config):
            if event["type"] == "token":
                yield event["data"]

    return RunnableGenerator(stream_answer).with_config({"run_name": "RAGChain"})
