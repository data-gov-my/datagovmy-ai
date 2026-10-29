# Docs assistant RAG chain – audit and modernisation plan

_Last updated: 2026-10-26 (implementation status in §8). Scope: `src/assistant/chain.py` (docs chat), with notes on `generate_meta.py`, `ingest.py`, `app.py`._

## 1. Current state

### Dependency gap

| Package | Pinned | Latest (PyPI, Oct 2026) | Notes |
| --- | --- | --- | --- |
| langgraph | 0.6.7 | 1.2.17 | Two majors behind. Needs langchain-core ≥ 1.4.7 |
| langchain | 0.3.27 | 1.4.7 | 1.x removed `langchain.retrievers`, `langchain.docstore`, `langchain.schema`, `langchain.indexes`, `langchain.text_splitter` |
| langchain-core | (transitive 0.3.x) | 1.7.0 | |
| langchain-openai | 0.3.33 | 1.7.2 | 1.x defaults GPT-6 + tools to the Responses API, ships model profiles |
| langchain-chroma | 0.2.6 | 1.1.0 | |
| langchain-community | 0.3.29 | 0.4.2 | Only `vect.py` (unused) needs it |
| langchain-classic | – | 1.0.8 | New home for `EnsembleRetriever`, `SQLRecordManager`, `index` |
| langserve | 0.3.2 | 0.3.3 | Maintenance mode; still allows langchain-core < 2 |
| openai | 1.108.0 | 3.38.0 | |
| chromadb | 1.1.0 | 1.5.9 | Keep server image and client versions in step |
| fastapi | 0.111.1 | 0.162.0 | |

Python: the Dockerfile uses 3.11, and every target package supports ≥ 3.10, so the base image doesn't need to change.

### How the chain works today (`chain.py`)

```
messages ─► format_messages ─┬─► MultiQueryRetriever: raw last msg ─► QueryExpand (mini, T=0.7) ─► 3× MMR k=4 ─► RRF ─► context
                             ├─► QueryRewrite (mini, T=0.7) ─► query
                             └─► history (unbounded)
                                                  ─► system(prompt+context) + history + rewritten query ─► gpt-4.1 ─► str
```

It is served through LangServe `add_routes(app, rag_chain, path="/chat")`, so the frontend depends on the LangServe `/chat/invoke` and `/chat/stream` protocol.

## 2. Audit findings

Ordered by impact on answer quality and reliability.

### Retrieval quality

1. **Query expansion ignores the conversation.** `QueryExpand` gets the *raw* last message. `QueryRewrite` runs *in parallel*, so its output never reaches retrieval. A follow-up like "what about the Python example?" or "boleh bagi contoh?" is embedded with no context, and the rewrite only changes the question the LLM sees. This is the biggest quality defect: retrieval should run on the rewritten, standalone query.
2. **The original question is never retrieved on.** Only the 3 LLM paraphrases are searched. When the expansion drifts, the literal query (often a dataset id like `fuelprice` or an endpoint name) is lost.
3. **The `EnsembleRetriever` is a workaround.** It is built with one retriever and 3 weights only to borrow `weighted_reciprocal_rank`. The pad/truncate logic then silently **drops any 4th+ query**. Plain RRF is about 10 lines and removes the dependency (which now lives in `langchain-classic`).
4. **No relevance threshold, filter or rerank.** MMR k=4 per query is fused into ≤ 12 chunks with no score cut-off, so irrelevant chunks are always injected. That is part of why the "apologise if not in context" rule is unreliable.
5. **English-only corpus with no query translation.** Only `*.en.mdx` is ingested, but the prompt promises Malay support. Malay questions are embedded against English chunks. The expansion step should always emit at least one English query.
6. **The expansion output is parsed with `split("\n")`.** Numbering or bullets ("1. …") leak into the queries. Use structured output (a list of strings).

### Generation / prompting

7. **The rewritten query replaces the user's words in the final prompt.** The answer model never sees what the user actually typed, and the rewrite runs at temperature 0.7. It should be used for retrieval only. _(Withdrawn after review: the rewrite is the jailbreak filter, see §8 Guardrails.)_
8. **History is unbounded.** `MAX_MESSAGES = 5` in `app.py` is defined but never used. Long chats grow cost and latency, and old turns dilute the context.
9. **Prompt hygiene.** `QA_DOCS_PREFIX` ends with a stray `\"`, the first rule ends with a stray `"`, and the rewrite prompt doesn't say "output only the question" (preambles leak). Context sits in the system message ahead of history. Moving it next to the latest user turn helps grounding on long chats.
10. **No citations.** Chunks carry `Source: <path>`, but the model isn't asked to cite, and the frontend can't render sources because the chain returns a bare string.

### Reliability / ops

11. **No retries or timeouts** on the 3 LLM calls and the Chroma calls. One OpenAI 5xx fails the whole request.
12. **No caching** of expansions or embeddings. Repeated FAQ-style questions pay full price every time.
13. **`app.py` bugs:** `HTTPException` is not imported (`/auth-token` would raise `NameError`). `/health` is defined twice. `allow_origins=os.getenv(...)` passes a *string*, so Starlette does substring matching instead of using the parsed list in `config.settings`. `NewChatReqest` is unused.
14. **Two separate Chroma and embedding clients** are created (chain and generate_meta) at import time. A Chroma outage at boot crashes the app.
15. **`generate_meta.py`:** `kwargs={"filter": ...}` on `ainvoke` is ignored (the filter is hard-coded anyway). A blocking `pd.read_csv(url)` runs inside an async node. `.sample(10)` raises when a dataset has fewer than 10 rows. It uses `print()` instead of logging.
16. **Dead code:** `vect.py` (Weaviate, unused) imports modules removed in langchain 1.x. `PromptTemplate`, `Dict`, `Any`, `Optional` are unused imports in `chain.py`.

### Evaluation

17. **There is no offline eval set.** Changes to prompts, models or retrieval can't be measured. LangSmith tracing is configured (`LANGCHAIN_*` env), but traces aren't being turned into a dataset.

## 3. What's new since langgraph 0.6 / langchain 0.3 that we should use

**LangGraph 1.0 → 1.2**

- **Stable 1.x API.** `StateGraph(..., context_schema=...)` with `Runtime[Context]` injection into nodes replaces passing config dicts around. Use it for per-request settings such as language and model overrides.
- **`set_node_defaults(retry_policy=, timeout=, error_handler=, cache_policy=)`** sets graph-wide retry, timeout and fallback in one place. Per-node `add_node(..., retry_policy=, cache_policy=, timeout=, error_handler=)` overrides it.
- **Node-level error handlers** (1.2). A failed retrieval or rewrite can fall back (for example, retrieve on the raw query) instead of failing the request.
- **Node caching** (`CachePolicy` + `compile(cache=...)`) lets us cache `rewrite`/`expand`/`retrieve` keyed on the normalised question. A Redis-backed cache is possible, and Redis is already in docker-compose.
- **`trace_policy` on `add_node`** (1.2.10) controls what each node sends to LangSmith, for example to redact user PII.
- **Streaming.** `stream_mode="messages"` streams tokens from the `generate` node only. `stream_events(version="v3")` (beta) gives typed events. `stream_mode="custom"` can push the cited sources to the UI before the answer starts.
- **Durability and checkpointers** (`langgraph-checkpoint-redis` 0.5 / `-postgres` 3.2). These are optional: the UI sends full history today, so stateless is fine for phase 1.

**LangChain 1.x**

- Legacy modules moved to `langchain-classic`. We should *drop* rather than port: `EnsembleRetriever` becomes our own RRF, `Document` comes from `langchain_core.documents`, `RecordManager`/`index` comes from `langchain_core.indexing` plus `langchain_classic.indexes.SQLRecordManager` (ingest only), and the splitters come from `langchain_text_splitters`.
- `init_chat_model("openai:gpt-6-sol")` with model profiles (`llm.profile`) gives capability checks, for example whether a model accepts `temperature`.
- `langchain-openai` 1.x: Responses API support and `reasoning={"effort": ...}`. The GPT-6 family rejects `temperature`.

## 4. OpenAI model update (done in this branch)

Model IDs come from the model profiles shipped in `langchain-openai` 1.7.2 (the newest family is GPT-6: Astra > Sol > Luna, released Sep 2026).

| Use | Before | After (default) | Override env |
| --- | --- | --- | --- |
| Docs answer | `gpt-4.1`, T=0 | `gpt-6-sol`, reasoning_effort `none` | `OPENAI_ANSWER_MODEL`, `OPENAI_ANSWER_REASONING_EFFORT` |
| Query expand + rewrite | `gpt-4.1-mini`, T=0.7 | `gpt-6-luna`, reasoning_effort `none` | `OPENAI_FAST_MODEL`, `OPENAI_FAST_REASONING_EFFORT` |
| Generate meta | `gpt-4o`, T=0.5 | `gpt-6-sol`, reasoning_effort `low` | `OPENAI_GENERATE_META_MODEL`, `OPENAI_GENERATE_META_REASONING_EFFORT` |
| Embeddings | `text-embedding-3-small` | unchanged | – |

Notes:

- GPT-6 models are reasoning models and **reject `temperature`**, so it is removed. `reasoning_effort="none"` is the closest match to the non-reasoning gpt-4.1 / 4.1-mini behaviour and keeps time-to-first-token low.
- `gpt-6.1-sol` (2026-09-29) is newer than `gpt-6-sol`, but its profile doesn't document the supported reasoning-effort levels. It is one env var away (`OPENAI_ANSWER_MODEL=gpt-6.1-sol`) once `none` is confirmed against the API.
- I checked the request payload with the *pinned* `langchain-openai` 0.3.33: it sends `reasoning_effort`, omits `temperature`, and structured output still uses `json_schema`. I haven't tested it against the live API; run a smoke test in staging first.
- Embeddings stay as they are. `langchain-openai` 1.7.2 lists no embedding model newer than `text-embedding-3-*`, and changing it would mean re-indexing.
- `gpt-4.1`/`gpt-4o` aren't retired yet. A third-party tracker reports that GPT-5-era models are being discontinued on 2026-12-11 (not verified against OpenAI: openai.com is blocked from this environment), so moving off the 4.x line now is the safe choice.

## 5. Target design (LangGraph 1.2)

```
START ─► contextualise ─► expand ─► retrieve (fan-out per query, Send) ─► fuse_and_filter ─► generate ─► END
              │                                                                │
              └── greeting/thanks/off-topic? ──► respond_direct ──────────────► END
```

State:

```python
class RAGState(TypedDict):
    messages: list[AnyMessage]           # last N turns (trimmed)
    standalone_question: str             # from contextualise
    queries: list[str]                   # original + standalone + ≤3 expansions (≥1 English)
    docs: Annotated[list[Document], operator.add]
    context: list[Document]              # fused, deduped, thresholded
    answer: str

class Context(TypedDict):                # runtime context, not state
    answer_model: str
    language: Literal["en", "ms"] | None
```

Nodes:

1. **contextualise**: `gpt-6-luna` with structured output `{standalone_question, intent: docs|dataset|smalltalk|off_topic, language}`. This one call replaces both `QueryRewrite` and the greeting/off-topic rules, and routes smalltalk and off-topic messages away from retrieval (saving roughly 2 LLM calls and 3 vector searches per greeting).
2. **expand**: structured output `list[str]`. Always includes the original and standalone question, and at least one English query. Cached with `CachePolicy(ttl=...)`.
3. **retrieve**: one `Send` per query (parallel). MMR plus a `relevance_score` threshold. Metadata filter on `source` when intent is `dataset`. `RetryPolicy` + `timeout`; the `error_handler` falls back to the raw question.
4. **fuse_and_filter**: 10-line RRF and dedup by chunk id, capped at about 6 chunks. Optional reranker later.
5. **generate**: `gpt-6-sol`. Sees the *user's original words* plus history plus context, and is asked to cite `[n]` sources. Streamed with `stream_mode="messages"`. The sources list is emitted via `stream_mode="custom"`.

Graph-wide: `set_node_defaults(retry_policy=RetryPolicy(max_attempts=3), timeout=30)` and `trim_messages` to the last `MAX_MESSAGES` turns.

## 6. Serving

LangServe is in maintenance mode, and streaming a graph through `add_routes` emits state chunks, not tokens. To keep the frontend working:

- **Phase 1 (compat):** keep `/chat` via `add_routes` on a thin runnable `graph.astream(..., stream_mode="messages")`, filtered to the `generate` node and mapped to string chunks. The existing UI contract is unchanged.
- **Phase 2:** add `/v2/chat` as a plain FastAPI `EventSourceResponse` (sse-starlette is already a dependency) streaming `{type: "token"|"sources"|"done"}`. Move the frontend over, then remove LangServe.

## 7. Phased plan

| Phase | Work | Exit criteria |
| --- | --- | --- |
| **0. Baseline** (½ day) | Fix LangSmith access (see §9). Build a dataset of 50–100 real questions from traces (EN + MS, follow-ups, dataset lookups, off-topic). Add evaluators: correctness (LLM judge vs reference), groundedness, retrieval hit-rate on expected `source`, refusal-when-unknown. Run on the **current** chain. | Baseline scores recorded |
| **1. Models** (this branch) | GPT-6 swap, env-overridable. | Smoke test in staging; eval ≥ baseline |
| **2. Dependency upgrade** (1 day) | Bump to langchain 1.4 / core 1.7 / langgraph 1.2 / langchain-openai 1.7 / langchain-chroma 1.1 / openai 3.x. Port imports (`langchain_core.documents`, `langchain_text_splitters`, `langchain_classic.indexes`). Delete `vect.py`. Replace `EnsembleRetriever` with local RRF. Fix the `app.py` bugs (#13). | App boots, ingest runs, eval unchanged |
| **3. LangGraph RAG** (2–3 days) | Implement §5. Fix findings #1–#10. | Eval: retrieval hit-rate and follow-up accuracy up; p50 latency ≤ baseline |
| **4. Serving** (1 day) | Phase 1 compat adapter, then `/v2/chat` with sources. | Frontend works unchanged, then migrates |
| **5. Hardening** | Node cache on Redis, `trace_policy` redaction, LangSmith online evaluators on sampled production traces, alerting on refusal rate. Port `generate_meta` to the same patterns (#15). | – |

## 8. Implementation status (this branch)

Phases 1, 2, 3 and 4 are implemented. Phase 0 (baseline eval) is still blocked on LangSmith access, so **nothing has been compared against real traffic yet**. Run the eval, or at least a staging soak, before production.

What changed versus the plan:

- **Graph** (`chain.py`): `analyse → retrieve (Send per query) → fuse → generate`, plus `refuse` for blocked, off-topic and over-long messages and for analysis failures. A single structured `gpt-6-luna` call returns the intent, language, a sanitised standalone question and English search queries, replacing both QueryRewrite and QueryExpand. Retrieval runs on the original question, the standalone rewrite and the expansions. Context is capped at `RAG_MAX_CONTEXT_DOCS` (default 8). See "Guardrails" below.
- **Fixed findings:** #1, #2, #3, #5, #6, #8, #9 (stray quotes), #11, #13, #14, #15, #16. **Withdrawn:** #7. Answering on the rewrite is deliberate: it is the jailbreak filter and carries the assumed context for terse questions (see Guardrails). The rewrite now comes from a structured call with no `temperature`. **Open:** #4 (relevance threshold/rerank: needs eval data to tune), #10 (citations in the answer text; sources are emitted separately on `/v2`), #12 (cache is per process; Redis-backed cache is phase 5), #17.
- **Reliability:** `analyse` has `RetryPolicy`, `TimeoutPolicy` and an `error_handler` that **fails closed** (a canned "try again" reply, no answer-model call). Its node cache (`RAG_CACHE_TTL_SECONDS`, default 900s) skips the LLM call for repeated first-turn questions, and fallbacks are not cached. `generate` has an idle timeout and no graph retry, because a retry after tokens have streamed would duplicate output.
- **Deviation:** retrieval does its own retry (`with_retry`), timeout and empty fallback *inside* the node, rather than using node policies. In langgraph 1.2.17 an `error_handler` doesn't stop the error being re-raised when a fanned-out (`Send`) sibling task fails; it only works for single tasks (reproduced in isolation). Retrieval isn't node-cached, so a failed search is never cached.
- **Serving:** `/chat` (LangServe) keeps the exact contract (`{"messages": [...]}` in, string chunks out) and the `RAGChain` root run name in LangSmith. The new `POST /v2/chat/stream` (same auth) is SSE: `sources` (`[{title, url}]`), then `token` events, then `end`.
- **No legacy flag:** the old LCEL chain couldn't run on langchain 1.x without porting its removed imports, so it was replaced rather than kept behind `DOCS_CHAIN`. To roll back, revert the branch: the Chroma index and ingest format are unchanged.
- **Dependencies:** `langchain-chroma` is held at **1.0.0** (not 1.1) because 1.1 needs chromadb ≥ 1.3.5, while the Chroma server image is 1.1.0. Bump the server image and client together later. `langchain`/`langchain-community`/`faiss-cpu`/`weaviate-client` are removed. `langchain-classic` is kept only for `SQLRecordManager` in ingest. `cryptography` and `sqlalchemy[asyncio]` were transitive before and are now pinned explicitly.
- **Behaviour changes to watch:**
  - CORS now uses the parsed origin list (exact match). Before, a string was passed, so substring matching applied. Check that `BACKEND_CORS_ORIGINS` in prod is a JSON list or comma-separated.
  - Off-topic and blocked messages get a short model-written decline (see Guardrails). Messages the analyser can't make sense of ("Gill", "J", random characters) are classed `unclear` and get a canned request to clarify, in English and Malay, since language detection on one word isn't reliable. The old chain's rewrite turned these into clarification questions that the answer model then "answered".
  - Only English and Malay are supported. Messages in other languages are rewritten and answered in English.
  - Neutral requests for official election or parliament data are classed as `docs` and still answered; tune this in `QUERY_ANALYSIS_PROMPT` if policy says otherwise.
- **Tests:** `tests/test_chain.py` (fake LLM and search, no network). Run with `python -m pytest tests` from the repo root. A FastAPI `TestClient` smoke test of `/chat/invoke`, `/chat/stream`, `/v2/chat/stream`, auth, CORS and `/health` passed locally with fake models.
- **Eval:** a 60-question set drawn from production traces (Aug–Oct 2026) plus synthetic red-team and false-positive cases is kept outside the repo, as it contains real user messages. Against real models and a September 2025 copy of the index, all 60 were routed as expected (intent, language, decline reason, expected sources), and the answers were reviewed by hand. Not yet compared against the old chain, and the index should be rebuilt before that comparison.

### Guardrails

As in the original chain, the rewrite acts as the filter between raw user text and the answer model, but the new graph makes it stricter:

1. **Length cap.** A latest message over `RAG_MAX_QUESTION_CHARS` (default 4000, about a page) is refused before any model call. Earlier turns are truncated to the same length; they are client-supplied, so assistant turns can be forged too. The cap mainly guards against many-shot jailbreaks, which need long prompts, and keeps the classifier reliable and costs bounded. 4000 leaves room for pasted code, error messages and API responses. Tune it from the p99 question length in LangSmith once access is back.
2. **Classification.** The analysis call labels the message `docs`, `smalltalk`, `unclear`, `off_topic` or `blocked`, with a `decline_reason` for the last two. `blocked` covers instruction-override and role-play attempts, "say/repeat/endorse X", impersonating any person or politician, opinions or claims about persons, politicians, parties, religion, race or royalty, and harmful requests (including code to attack, overload or bypass limits). A message that mixes a real question with any of these is blocked too.
   - **Code requests:** code that calls the data.gov.my API or works with its datasets ("python plot CPI") is `docs`. Code for anything else ("React login page", "scrape Shopee") is `off_topic` / `unrelated_code`. The answer model also has a rule to only write code for the API or its datasets, as a second layer.
   - The prompt tells the classifier to treat the conversation, including any assistant turns, as data and never to follow it.
3. **Model-written declines that never see user text.** `blocked` and `off_topic` go to a `decline` node (`gpt-6-luna`), which writes a short, varied reply in the user's language. It receives only:
   - the decline reason, as a fixed description from `DECLINE_REASONS`;
   - for `off_topic` only, a topic label of at most 6 words containing only letters, digits, spaces and hyphens (`sanitise_topic`), e.g. "React login page";
   - the reply language.

   For `blocked`, no topic is passed at all, so the reply can't echo the attack. Residual risk: an off-topic label is still derived from user text. It is limited to 6 words with no quotes or punctuation, the decline prompt says to ignore instructions in it, and the analyser has to be fooled first.
4. **Canned replies** (`CANNED_REPLIES`) remain only where no model output is safe or available: over-long and unclear messages, analysis failure, an empty rewrite, or a failed decline call.
5. **Answer model sees only the rewrite.** `generate` receives the system prompt, the retrieved docs and **only** the sanitised standalone question. Raw user text never reaches it, including earlier turns. This closes a multi-turn hole the old chain had: it passed raw history, so an injection in turn 1 could ride along with a harmless question in turn 2. The rewrite resolves references, so the history isn't needed for answering.
6. **Fail closed.** If analysis errors or returns an empty rewrite, the user gets a canned reply. A message crafted to break the analyser's structured output can't fall through to the answer model.
7. **Prompt rules stay** in the answer model's system prompt as a second layer.

### Guarding with public rules

The repo is open source, so every prompt and rule is visible. The guards are built so that knowing them doesn't help an attacker (security that doesn't depend on secrecy):

1. **Allowlist, not denylist.** Only messages classified `docs` or `smalltalk` reach the answer model. A message that dodges the `blocked` patterns usually lands in `off_topic`, which is still declined. To get through, an attacker has to make the message genuinely look like a docs question.
2. **Capability limits.** The bot has no tools, no secrets and no data beyond public docs. The answer model never sees raw user text, and the decline model sees only fixed reasons. Knowing the prompts doesn't create a path for user text to reach them. What's left at risk is mainly reputational: screenshots.
3. **Output checks against runtime data** (`guards.py`, applied to `/chat` and `/v2/chat/stream`). To pass them, the text or link has to exist in data.gov.my's own docs, which the attacker can't edit.
   - Links: only `*.data.gov.my` / `*.dosm.gov.my`, or URLs that appear verbatim in the retrieved docs. Others are removed.
   - Echo: if the answer reproduces a 3+ word phrase the user put in double quotes, and the phrase isn't in the retrieved docs, the answer stops and a canned refusal is appended. This stops "how do I query the dataset called "<statement>"" smuggling.
   - Text is released a sentence or line at a time, so streaming stays live and each piece is checked before display.
4. **Persona.** The system prompt now says the bot is an automated documentation assistant, not a spokesperson, and must not make statements for the government or any person. The old wording, "you are a government representative", gave screenshots more authority.
5. **Make probing expensive** (not done yet). Classifiers are probabilistic and attackers iterate. Next steps:
   - per-client rate limits on `/chat`, with a cooldown after repeated blocks;
   - logging and alerting on block-rate spikes;
   - a red-team regression set run in CI on every prompt change.
6. **Optional private layer.** A deployment can add rules outside the repo, e.g. a prompt pulled from LangSmith Hub, or the OpenAI moderation endpoint, whose behaviour isn't published. Treat these as extra friction only; nothing above depends on them.

Residual gaps:
- The echo check only covers explicitly quoted phrases. An unquoted statement woven into a question still relies on the analyser, which drops non-question content from the rewrite.
- An attacker who learns the boundaries can still probe for classifier misses. Hence item 5.

**Terse questions.** The analysis prompt restores the old rewrite rule "make reasonable assumptions that the question is about the data.gov.my API or data catalogue", with English and Malay examples (`"fuel price?"`, `"data penduduk negeri"`, `"limit?"`). Because the answer model now answers the rewrite, the assumed context reaches the answer as well as the search.

**Not done / next:**
- Build a red-team set in the Phase 0 dataset (English and Malay jailbreaks, "say X" prompts, impersonation, political bait, plus legitimate election and parliament data questions to catch false positives). Track block rate and false-block rate per release.
- Optional output check: run the OpenAI moderation endpoint, or a cheap classifier, on the final answer before showing it. This adds latency or breaks token streaming, so only do it if red-teaming shows leaks.
- Rate limiting per client on `/chat` (none today).

## 9. LangSmith traces – blocked

I attempted to pull example questions from the `datagovmy-ai-docs` tracing project, but every project and run query to the LangSmith workspace (`Workspace 1`) returned **`403 Forbidden – no plan found`**. The workspace is visible, but the organisation appears to have no active plan, so trace data can't be read via the API. Restore the plan (or grant an API key on an org with one) and then:

1. Pull root runs for `RAGChain` over the last 30–90 days.
2. Cluster the questions (LangSmith Insights can do this) into: endpoint/how-to, specific dataset lookups, code samples by language, Malay questions, follow-ups, smalltalk/off-topic.
3. Sample those clusters into the Phase 0 dataset, and use trace feedback and refusals to pick the failure cases to optimise for.

Until then, the question categories above come from the ingested corpus (developer docs, data catalogue metadata, the `sample-query` code examples) and the prompt rules.
