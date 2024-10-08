from cryptography.fernet import Fernet
import os

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
        # invalidate cache
        await FastAPICache.clear(namespace="token")

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


key_manager = APIKeyManager(
    key_file_path=os.getenv("KEY_FILE"), encryption_key=os.getenv("ENCRYPT_KEY")
)


@cache(namespace="token")
async def retrieve_token() -> str:
    return key_manager.get_key()


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
    saved_token = await retrieve_token()
    if auth is None or (token := auth.credentials) != saved_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token missing or unknown",
        )
    return token
