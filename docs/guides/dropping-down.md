# Dropping down

fyro generates the common case. When a model's shape and the API you want stop matching, step down to plain FastAPI and SQLAlchemy, one piece at a time. Nothing here fights the framework: each escape hatch is a supported path.

| You want | Use |
|---|---|
| An endpoint that is not CRUD | your own route in `<app>/routes.py` |
| A query the manager cannot express | SQLAlchemy on the request's session |
| A different JSON shape for one model | `Meta.read_schema` / `create_schema` / `update_schema` |
| No generated API for a model at all | `Meta.api = False`, then your own routes |

## Your own routes

`<app>/routes.py` is an ordinary FastAPI router, mounted at `/api/<app>` before the automatic routes. Dependencies, response models, status codes: everything FastAPI offers works as usual.

```python
from fastapi import APIRouter

from blog.models import Post

router = APIRouter()


@router.get("/post/stats")
async def stats():
    return {"posts": await Post.objects.count()}
```

## SQLAlchemy on the request's session

A `Model` is a regular SQLAlchemy mapped class, so `select(Post)`, joins, aggregates and window functions all work. Ask for the request's session with `fyro.db.DBSession`:

```python
from fastapi import APIRouter
from sqlalchemy import func, select

import fyro.db
from blog.models import Post

router = APIRouter()


@router.get("/raw/published")
async def published(db: fyro.db.DBSession):
    stmt = select(func.count()).select_from(Post).where(Post.published.is_(True))
    return {"published": await db.scalar(stmt)}
```

It is the same session `Post.objects` uses, so both run in one transaction: committed just before the response when the status is below 400, rolled back otherwise. Outside a route (a helper function, a script inside `async with fyro.db.session():`), `fyro.db.current_session()` returns that same session.

Do not commit or close it yourself; fyro owns its lifetime.

## Replacing a generated schema

`Meta.*_exclude` drops fields. When the shape itself must differ, hand fyro your own Pydantic model. The automatic API then validates input with it, or serializes output through it:

```python
from pydantic import BaseModel

from fyro import Field, Model


class PostSummary(BaseModel):
    id: int
    title: str


class Post(Model):
    title: str = Field(max_length=200)
    body: str

    class Meta:
        read_schema = PostSummary
```

fyro builds it from the database row (`from_attributes`), so a plain `BaseModel` is enough. Fields it does not declare are never returned, and the list endpoint offers filters only for fields it declares.

## Turning the API off

`Meta.api = False` removes a model's automatic routes; the table, schemas, manager and admin stay. Write the routes you want in `routes.py`, using `Post.Create` / `Post.Read` (or your own models) and either `Post.objects` or `DBSession`.
