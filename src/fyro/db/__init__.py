from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fyro.db.engine import get_engine
from fyro.db.session import current_session, session

__all__ = [
    "DBSession",
    "DoesNotExist",
    "MultipleObjectsReturned",
    "NotLoaded",
    "current_session",
    "get_engine",
    "session",
]


class DoesNotExist(Exception):
    """Base of every `Model.DoesNotExist`."""


class MultipleObjectsReturned(Exception):
    """Base of every `Model.MultipleObjectsReturned`."""


class NotLoaded(Exception):
    """A ForeignKey read before it was loaded with `select_related()`."""


async def _request_session() -> AsyncSession:
    return current_session()


# The request's SQLAlchemy session as a dependency: `async def view(db: DBSession)`.
# Async, so FastAPI runs it in the request's context, where the session lives.
DBSession = Annotated[AsyncSession, Depends(_request_session)]
