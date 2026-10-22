from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_current: ContextVar[AsyncSession | None] = ContextVar("fyro_session", default=None)


def current_session() -> AsyncSession:
    active = _current.get()
    if active is None:
        raise RuntimeError(
            "No database session. Inside a request fyro provides one; "
            "in scripts and tests use `async with fyro.db.session():`."
        )
    return active


def _new_session() -> AsyncSession:
    from fyro.db.engine import get_engine  # local: engine.py imports this module

    return AsyncSession(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncIterator[None]:
    """Open a session for scripts and tests: commit on clean exit, roll back on error.

    Inside an active session (a request, or an outer `session()`), reuse it and let the
    outer owner commit.
    """
    if _current.get() is not None:
        yield
        return
    async with _new_session() as active:
        token = _current.set(active)
        try:
            yield
            await active.commit()
        except BaseException:
            await active.rollback()
            raise
        finally:
            _current.reset(token)


class SessionMiddleware:
    """One session per HTTP request.

    Commits just before the response starts when the status is < 400, so a failed commit
    becomes a 500 instead of a 200 for unsaved data. Rolls back on 4xx/5xx or an exception.
    Writes made while streaming a body, after the response has started, are not committed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        async with _new_session() as active:
            token = _current.set(active)

            async def send_after_commit(message: Message) -> None:
                if message["type"] == "http.response.start":
                    if message["status"] < 400:
                        await active.commit()
                    else:
                        await active.rollback()
                await send(message)

            try:
                await self.app(scope, receive, send_after_commit)
            except BaseException:
                await active.rollback()
                raise
            finally:
                _current.reset(token)
