import asyncio
import json

from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

import auth
from auth import APIKeyManager, SSMTokenStore


class ParameterNotFound(Exception):
    pass


class FakeSSM:
    """Stands in for boto3's SSM client; shared between "tasks" like the real one"""

    class exceptions:
        ParameterNotFound = ParameterNotFound

    def __init__(self, value=None):
        self.value = value
        self.reads = 0

    def get_parameter(self, Name, WithDecryption):
        self.reads += 1
        if self.value is None:
            raise ParameterNotFound()
        return {"Parameter": {"Value": self.value}}

    def put_parameter(self, Name, Value, Type, Overwrite):
        assert Type == "SecureString" and Overwrite
        self.value = Value


def valid(store, token, monkeypatch):
    monkeypatch.setattr(auth, "key_manager", store)
    return asyncio.run(auth.is_valid_token(token))


def test_seeded_plain_value_is_current_token(monkeypatch):
    store = SSMTokenStore("/p", client=FakeSSM("tok-1"))
    assert valid(store, "tok-1", monkeypatch)
    assert not valid(store, "tok-2", monkeypatch)


def test_rotation_keeps_previous_token(monkeypatch):
    ssm = FakeSSM("tok-1")
    store = SSMTokenStore("/p", client=ssm)
    asyncio.run(store.update_key("tok-2"))
    assert json.loads(ssm.value) == {"current": "tok-2", "previous": "tok-1"}
    assert valid(store, "tok-1", monkeypatch)
    assert valid(store, "tok-2", monkeypatch)

    # The Lambda retrying with the same token must not drop the previous one
    asyncio.run(store.update_key("tok-2"))
    assert json.loads(ssm.value) == {"current": "tok-2", "previous": "tok-1"}

    asyncio.run(store.update_key("tok-3"))
    assert not valid(store, "tok-1", monkeypatch)


def test_task_with_stale_cache_accepts_new_token(monkeypatch):
    ssm = FakeSSM("tok-1")
    task_a, task_b = SSMTokenStore("/p", client=ssm), SSMTokenStore("/p", client=ssm)
    assert valid(task_b, "tok-1", monkeypatch)  # task B caches the old token

    asyncio.run(task_a.update_key("tok-2"))  # the Lambda's call reaches task A
    task_b._fetched_at -= SSMTokenStore.MIN_REFRESH_SECONDS + 1
    assert valid(task_b, "tok-2", monkeypatch)


def test_unknown_tokens_refresh_at_most_once_per_interval(monkeypatch):
    ssm = FakeSSM("tok-1")
    store = SSMTokenStore("/p", client=ssm)
    assert valid(store, "tok-1", monkeypatch)
    reads = ssm.reads
    for _ in range(20):
        assert not valid(store, "guess", monkeypatch)
    assert ssm.reads == reads  # within MIN_REFRESH_SECONDS of the last read


def test_missing_parameter_rejects_everything(monkeypatch):
    store = SSMTokenStore("/p", client=FakeSSM(None))
    assert not valid(store, "anything", monkeypatch)


def test_non_ascii_token_is_rejected_not_an_error(monkeypatch):
    store = SSMTokenStore("/p", client=FakeSSM("tok-1"))
    assert not valid(store, "tokén", monkeypatch)


def test_key_file_mode_unchanged(monkeypatch, tmp_path):
    # ENVIRONMENT=dev switches to MASTER_TOKEN_KEY; a local .env may set it
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    key_file = tmp_path / "key.bin"
    key_file.write_text("tok-file")
    FastAPICache.init(InMemoryBackend(), prefix="")
    asyncio.run(FastAPICache.clear())
    manager = APIKeyManager(key_file_path=str(key_file), encryption_key=None)
    assert valid(manager, "tok-file", monkeypatch)
    assert not valid(manager, "other", monkeypatch)
