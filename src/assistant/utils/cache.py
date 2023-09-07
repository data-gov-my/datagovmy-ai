from fastapi_cache.decorator import cache

from config import *


def nocache(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


if settings.ENVIRONMENT == "dev":
    # bypass cache in dev environment
    cache = nocache
else:
    cache = cache
