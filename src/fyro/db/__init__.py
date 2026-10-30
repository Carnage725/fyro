from fyro.db.engine import get_engine
from fyro.db.session import session

__all__ = ["DoesNotExist", "MultipleObjectsReturned", "NotLoaded", "get_engine", "session"]


class DoesNotExist(Exception):
    """Base of every `Model.DoesNotExist`."""


class MultipleObjectsReturned(Exception):
    """Base of every `Model.MultipleObjectsReturned`."""


class NotLoaded(Exception):
    """A ForeignKey read before it was loaded with `select_related()`."""
