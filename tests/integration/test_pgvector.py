"""DB bootstrap + ingest + API retrieval against a real PostgreSQL/pgvector.

Skipped unless TEST_PG_SUPERUSER_DSN points at a disposable server, e.g.

    docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=pw pgvector/pgvector:0.8.7-pg17
    TEST_PG_SUPERUSER_DSN="host=localhost port=55432 user=postgres password=pw" \
        python -m pytest tests/integration

The superuser only creates an RDS-like admin (CREATEDB, CREATEROLE, not a
superuser); everything else runs as that admin, as it would on RDS.
"""

import asyncio
import os
import uuid

import psycopg
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from psycopg import sql

DSN = os.getenv("TEST_PG_SUPERUSER_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_SUPERUSER_DSN not set")

EMBEDDINGS = DeterministicFakeEmbedding(size=1536)
DOCS = [
    Document(page_content="Use the id parameter to query a dataset", metadata={"source": "docs/api.mdx", "header": "API"}),
    Document(page_content="Rate limits apply per API token", metadata={"source": "docs/limits.mdx", "header": "Limits"}),
    Document(page_content="Consumer price index, monthly, by state", metadata={"source": "dc_meta", "header": "CPI"}),
    Document(page_content="Fuel prices, weekly", metadata={"source": "dc_meta", "header": "Fuel"}),
]  # fmt: skip


class ParameterNotFound(Exception):
    pass


class FakeSSM:
    class exceptions:
        ParameterNotFound = ParameterNotFound

    def __init__(self):
        self.params = {}

    def get_parameter(self, Name, WithDecryption):
        if Name not in self.params:
            raise ParameterNotFound()
        return {"Parameter": {"Value": self.params[Name]}}

    def put_parameter(self, Name, Value, Type, Overwrite):
        assert Type == "SecureString"
        self.params[Name] = Value


@pytest.fixture(scope="module")
def server():
    """A fresh RDS-like admin on the test server; dropped afterwards."""
    suffix = uuid.uuid4().hex[:8]
    admin, password = f"rdsadmin_{suffix}", "admin-pw"
    conn = psycopg.connect(DSN, dbname="postgres", autocommit=True)
    params = psycopg.conninfo.conninfo_to_dict(DSN)
    conn.execute(
        sql.SQL("CREATE ROLE {} LOGIN CREATEDB CREATEROLE PASSWORD {}").format(
            sql.Identifier(admin), sql.Literal(password)
        )
    )
    # RDS creates the initial database (dgmyai) owned by the admin
    conn.execute(
        sql.SQL("CREATE DATABASE dgmyai OWNER {}").format(sql.Identifier(admin))
    )
    # pgvector isn't a trusted extension here; on RDS the admin can create it
    with psycopg.connect(DSN, dbname="dgmyai", autocommit=True) as db:
        db.execute("CREATE EXTENSION IF NOT EXISTS vector")
    yield {
        "host": params.get("host", "localhost"),
        "port": params.get("port", "5432"),
        "admin": admin,
        "password": password,
    }
    for db in ("dgmyai", "dgmyai_staging"):
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))  # fmt: skip
    for env in ("prod", "staging"):
        for kind in ("app", "ingest"):
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(f"dgmyai_{env}_{kind}")))  # fmt: skip
    conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(admin)))
    conn.close()


@pytest.fixture(scope="module")
def ssm(server):
    from db_bootstrap import bootstrap

    fake = FakeSSM()
    for env in ("prod", "staging"):
        if env == "staging":
            # Staging's database is created by the bootstrap; give it pgvector
            # the same way the fixture did for dgmyai (superuser locally)
            bootstrap_env(server, fake, env)
            with psycopg.connect(DSN, dbname="dgmyai_staging", autocommit=True) as db:
                db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        bootstrap(
            env=env,
            host=server["host"],
            port=server["port"],
            admin_user=server["admin"],
            admin_password=server["password"],
            ssm=fake,
            param_prefix=f"/datagovmy-ai/{env}",
            sslmode="disable",
        )
    return fake


def bootstrap_env(server, fake, env):
    """First pass for staging: creates the database (extension added after)"""
    from db_bootstrap import bootstrap

    try:
        bootstrap(
            env=env,
            host=server["host"],
            port=server["port"],
            admin_user=server["admin"],
            admin_password=server["password"],
            ssm=fake,
            param_prefix=f"/datagovmy-ai/{env}",
            sslmode="disable",
        )
    except psycopg.errors.InsufficientPrivilege:
        pass  # CREATE EXTENSION, locally only


def url(ssm, env, name):
    return ssm.params[f"/datagovmy-ai/{env}/{name}"]


def run_ingest(monkeypatch, ingest_url):
    import ingest
    from models import pgvector_store

    monkeypatch.setenv("DATABASE_URL_INGEST", ingest_url)
    monkeypatch.setattr(
        ingest,
        "get_vectorstore",
        lambda u, async_mode: pgvector_store(u, async_mode, EMBEDDINGS),
    )
    from langchain_core import indexing

    results = []
    real_index = indexing.index
    monkeypatch.setattr(
        ingest, "index", lambda *a, **kw: results.append(real_index(*a, **kw)) or results[-1]
    )  # fmt: skip
    ingest.run_index(DOCS, "docs")
    return results[-1]


def test_bootstrap_writes_both_urls_per_env(ssm):
    for env, db in (("prod", "dgmyai"), ("staging", "dgmyai_staging")):
        assert url(ssm, env, "DATABASE_URL").endswith(f"/{db}?sslmode=disable")
        assert f"dgmyai_{env}_app:" in url(ssm, env, "DATABASE_URL")
        assert f"dgmyai_{env}_ingest:" in url(ssm, env, "DATABASE_URL_INGEST")


def test_ingest_indexes_then_skips_unchanged(ssm, monkeypatch):
    first = run_ingest(monkeypatch, url(ssm, "staging", "DATABASE_URL_INGEST"))
    assert first["num_added"] == len(DOCS)
    second = run_ingest(monkeypatch, url(ssm, "staging", "DATABASE_URL_INGEST"))
    assert second["num_added"] == 0 and second["num_skipped"] == len(DOCS)


def test_api_user_searches_with_filter_but_cannot_write(ssm, monkeypatch):
    from models import pgvector_store

    run_ingest(monkeypatch, url(ssm, "staging", "DATABASE_URL_INGEST"))
    store = pgvector_store(url(ssm, "staging", "DATABASE_URL"), True, EMBEDDINGS)

    async def check():
        docs = await store.amax_marginal_relevance_search("prices", k=2)
        assert len(docs) == 2
        meta = await store.amax_marginal_relevance_search(
            "prices", k=4, filter={"source": "dc_meta"}
        )
        assert {d.metadata["source"] for d in meta} == {"dc_meta"}
        with pytest.raises(Exception, match="permission denied"):
            await store.aadd_documents([Document(page_content="x", metadata={"source": "x"})])  # fmt: skip

    asyncio.run(check())


def test_staging_users_cannot_reach_prod_database(ssm):
    staging_app = url(ssm, "staging", "DATABASE_URL")
    to_prod = staging_app.replace("/dgmyai_staging?", "/dgmyai?").replace(
        "postgresql+psycopg://", "postgresql://"
    )
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(to_prod).close()


def test_rerun_keeps_existing_credentials(server, ssm):
    from db_bootstrap import bootstrap

    before = dict(ssm.params)
    bootstrap(
        env="staging",
        host=server["host"],
        port=server["port"],
        admin_user=server["admin"],
        admin_password=server["password"],
        ssm=ssm,
        param_prefix="/datagovmy-ai/staging",
        sslmode="disable",
    )
    assert ssm.params == before


def test_ingest_lock_blocks_a_second_run(ssm):
    from ingest import single_run_lock

    ingest_url = url(ssm, "prod", "DATABASE_URL_INGEST")
    with single_run_lock(ingest_url) as first:
        assert first
        with single_run_lock(ingest_url) as second:
            assert not second
    with single_run_lock(ingest_url) as again:
        assert again
