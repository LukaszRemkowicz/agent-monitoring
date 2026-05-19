from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar, overload

from db.lifecycle import database_lifespan

P = ParamSpec("P")
T = TypeVar("T")
AsyncCallable = Callable[P, Coroutine[Any, Any, T]]


def as_async() -> Callable[[AsyncCallable[P, T]], Callable[P, T]]:
    def decorator(func: AsyncCallable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return asyncio.run(func(*args, **kwargs))

        return wrapper

    return decorator


@overload
def db[**P, T](func: AsyncCallable[P, T]) -> AsyncCallable[P, T]: ...


@overload
def db() -> Callable[[AsyncCallable[P, T]], AsyncCallable[P, T]]: ...


def db[**P, T](
    func: AsyncCallable[P, T] | None = None,
) -> AsyncCallable[P, T] | Callable[[AsyncCallable[P, T]], AsyncCallable[P, T]]:
    def decorator(wrapped: AsyncCallable[P, T]) -> AsyncCallable[P, T]:
        @wraps(wrapped)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            async with database_lifespan():
                return await wrapped(*args, **kwargs)

        return wrapper

    if func is None:
        return decorator
    return decorator(func)
