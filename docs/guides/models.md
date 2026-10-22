# Models

A model is a class in `<app>/models.py`. One class gives you the table, three Pydantic schemas, API routes and an admin page.

```python
import enum
from datetime import datetime
from uuid import UUID, uuid4

from fyro import Field, Model


class Status(enum.Enum):
    DRAFT = "draft"
    LIVE = "live"


class Post(Model):
    title: str = Field(max_length=200)
    slug: str = Field(max_length=50, unique=True)
    body: str
    secret: str = Field(write_only=True)
    views: int = Field(default=0, ge=0, read_only=True)
    status: Status = Status.DRAFT
    subtitle: str | None = None
    token: UUID = Field(default_factory=uuid4)
    created: datetime = Field(default_factory=datetime.now)
```

## Table

- The table name is `<app>_<class in snake_case>`: `Post` in `blog` is `blog_post`, `BlogPost` is `blog_blog_post`.
- An `id` (bigint, auto-increment, read-only) is added unless a field sets `primary_key=True`.
- `T | None` makes a nullable column; plain `T` is `NOT NULL`.

## Supported types

| Python | Postgres |
|---|---|
| `str` with `max_length` | `VARCHAR(n)` |
| `str` | `TEXT` |
| `int` | `INTEGER` (values outside its range are rejected with a 422) |
| `bool`, `float`, `Decimal`, `date`, `UUID` | `BOOLEAN`, `FLOAT`, `NUMERIC`, `DATE`, `UUID` |
| `datetime` | `TIMESTAMPTZ` |
| an `Enum` | `VARCHAR(255)` holding the member's **value** (no Postgres enum type) |

Any of these may be `T | None`. Other types raise `TypeError` when the class is defined, naming the model and field. Text can never contain a NUL character (Postgres cannot store it); such input is a 422.

!!! warning "Removing an enum member"
    Adding members needs no migration. Before removing one, update the rows that use it: reading a row whose stored value is no longer in the enum raises `LookupError`.

## `Field(...)`

| Argument | Meaning |
|---|---|
| `default`, `default_factory` | Python-side default (the database holds no default). |
| `primary_key`, `unique`, `index` | Column options. |
| `max_length` | Column length and validation. |
| `read_only` | Never accepted as input (not in `Create`/`Update`). |
| `write_only` | Never returned (not in `Read`), e.g. a password hash or secret. |
| `ge`, `le`, `min_length`, `pattern`, `description` | Passed to the Pydantic schemas. |

A field cannot be both `read_only` and `write_only`.

## Schemas

Every model gets three Pydantic classes:

- `Post.Create`: all fields except `read_only`. Required unless they have a default.
- `Post.Read`: all fields except `write_only`, readable from model instances (`from_attributes`).
- `Post.Update`: the `Create` fields, all optional. An explicit `null` on a required field is rejected; use `model_dump(exclude_unset=True)`.

## `Meta`

```python
class Post(Model):
    ...

    class Meta:
        table_name = "posts"                  # instead of blog_post
        api = False                           # no automatic API routes
        permissions = {"write": "superuser"}  # see Auth and permissions
        admin = False                         # hide from the admin
        list_display = ("id", "title")        # admin columns
        search_fields = ("title",)            # ?search= and admin search (text fields)
        read_exclude = ("body",)              # drop a field from Read
        create_exclude = (...)
        update_exclude = (...)
        create_schema = MyCreate              # replace a schema entirely
        read_schema = MyRead
        update_schema = MyUpdate
```

Unknown `Meta` keys and field names that do not exist are errors at class definition.
