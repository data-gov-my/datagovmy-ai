from cryptography.fernet import Fernet
import asyncio
import hmac
import json
import os
import time

from utils.cache import cache

from fastapi import Depends, status, HTTPException
from fastapi.security.http import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_cache import FastAPICache

get_bearer_token = HTTPBearer(auto_error=False)


class APIKeyManager:
    def __init__(self, key_file_path, encryption_key):
        self.key_file_path = key_file_path
        self.encryption_key = encryption_key

    def encrypt_key(self, key):
        fernet = Fernet(self.encryption_key)
        encrypted_key = fernet.encrypt(key.encode())
        return encrypted_key

    def decrypt_key(self, encrypted_key):
        fernet = Fernet(self.encryption_key)
        decrypted_key = fernet.decrypt(encrypted_key).decode()
        return decrypted_key

    async def update_key(self, api_key):
        # encrypted_key = self.encrypt_key(api_key)
        with open(self.key_file_path, "w") as file:
            file.write(api_key)
        # invalidate cache — clear() without namespace is more reliable than namespace-scoped clear
        await FastAPICache.clear()

    def get_key(self):
        if os.getenv("ENVIRONMENT") == "dev":
            return os.getenv("MASTER_TOKEN_KEY")
        try:
            with open(self.key_file_path, "r") as file:
                api_key = file.read()
                # api_key = self.decrypt_key(encrypted_key)
                return api_key
        except FileNotFoundError:
            return None


class SSMTokenStore:
    """Rolling token in an SSM SecureString, shared by every API task (ECS).

    /auth-token is called once by the token-roll Lambda and reaches a single
    task, so the token can't live in a local file. The parameter holds
    {"current": ..., "previous": ...}: a task still holding the old pair keeps
    accepting the old token, and a task that sees an unknown token re-reads
    the parameter once (rate limited) before rejecting it, so a client using
    the new token is never turned away by a stale cache. A plain string value
    (seeded by an operator) is read as the current token.
    """

    TTL_SECONDS = 60
    MIN_REFRESH_SECONDS = 5

    def __init__(self, param_name: str, client=None):
        self.param_name = param_name
        self._client = client
        self._tokens: list[str] = []
        self._fetched_at = float("-inf")

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("ssm")
        return self._client

    def _read(self) -> list[str]:
        try:
            value = self.client.get_parameter(
                Name=self.param_name, WithDecryption=True
            )["Parameter"]["Value"]
        except self.client.exceptions.ParameterNotFound:
            return []
        try:
            data = json.loads(value)
            tokens = [data.get("current"), data.get("previous")]
        except (json.JSONDecodeError, AttributeError):
            tokens = [value]
        return [t for t in tokens if t]

    async def get_tokens(self, refresh: bool = False) -> list[str]:
        age = time.monotonic() - self._fetched_at
        if age > self.TTL_SECONDS or (refresh and age > self.MIN_REFRESH_SECONDS):
            self._tokens = await asyncio.to_thread(self._read)
            self._fetched_at = time.monotonic()
        return self._tokens

    async def update_key(self, api_key: str):
        tokens = await asyncio.to_thread(self._read)
        current = tokens[0] if tokens else None
        previous = tokens[1] if len(tokens) > 1 else None
        if api_key != current:  # a repeated call must not drop the previous token
            previous = current
        value = json.dumps({"current": api_key, "previous": previous})
        await asyncio.to_thread(
            self.client.put_parameter,
            Name=self.param_name,
            Value=value,
            Type="SecureString",
            Overwrite=True,
        )
        self._tokens = [t for t in (api_key, previous) if t]
        self._fetched_at = time.monotonic()


def build_key_manager() -> APIKeyManager | SSMTokenStore:
    # AUTH_TOKEN_PARAM is set on the ECS platform; the Singapore host keeps
    # using KEY_FILE until it's decommissioned
    if param := os.getenv("AUTH_TOKEN_PARAM"):
        return SSMTokenStore(param)
    return APIKeyManager(
        key_file_path=os.getenv("KEY_FILE"), encryption_key=os.getenv("ENCRYPT_KEY")
    )


key_manager = build_key_manager()


@cache(namespace="token", expire=300)
async def retrieve_token() -> str:
    return key_manager.get_key()


def _matches(token: str, candidates: list[str]) -> bool:
    # bytes: compare_digest rejects non-ASCII str, which a client can send
    return any(hmac.compare_digest(token.encode(), c.encode()) for c in candidates)


async def is_valid_token(token: str) -> bool:
    if isinstance(key_manager, SSMTokenStore):
        return _matches(token, await key_manager.get_tokens()) or _matches(
            token, await key_manager.get_tokens(refresh=True)
        )
    saved_token = await retrieve_token()
    return saved_token is not None and _matches(token, [saved_token])


async def get_master_token(
    auth: HTTPAuthorizationCredentials = Depends(get_bearer_token),
) -> str:
    """Get master token for auth update endpoint."""
    if auth is None or (token := auth.credentials) != os.getenv("MASTER_TOKEN_KEY"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token missing or unknown",
        )
    return token


async def get_token(
    auth: HTTPAuthorizationCredentials = Depends(get_bearer_token),
) -> str:
    """Get API token for chat endpoint."""
    if auth is None or not await is_valid_token(token := auth.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token missing or unknown",
        )
    return token
