# datagovmy AI assitant

AI assistant backends for datagovmy

- `chain.py`: docs assistant RAG graph (LangGraph), served at `/chat` (LangServe)
- `generate_meta.py`: dataset metadata generation graph, served at `/generate-meta`
- `ingest.py`: refreshes the Chroma index (run by the cron container)
- `models.py`: OpenAI model ids (overridable via env, see `.env.example`) and the shared Chroma vectorstore

See `docs/rag-modernisation-plan.md` for the audit and design notes.

## Tests

From the repo root (no network or API keys needed):

```bash
pip install -r src/assistant/requirements.txt pytest
python -m pytest tests
```
