from fastapi_cache.decorator import cache
import os


def nocache(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


if os.getenv("ENVIRONMENT") == "dev":
    # bypass cache in dev environment
    cache = nocache
else:
    cache = cache
