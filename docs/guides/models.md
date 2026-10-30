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

## Links between models

```python
from fyro import Field, ForeignKey, ManyToMany, Model
from fyro.auth.models import User


class Tag(Model):
    name: str = Field(max_length=50, unique=True)

    def __str__(self) -> str:          # how the admin shows a tag
        return self.name


class Post(Model):
    title: str = Field(max_length=200)
    author: User = ForeignKey(on_delete="cascade")
    editor: User | None = ForeignKey(on_delete="set_null", default=None)
    tags: list[Tag] = ManyToMany()
```

### `ForeignKey`: one row

`author: User = ForeignKey(on_delete=...)` adds the column **`author_id`** (same type as the target's primary key, indexed, with a foreign key constraint). `post.author` is the linked row, once loaded (see [Querying](querying.md#links)). `User | None` makes the link optional.

`on_delete` is required and enforced by Postgres, so bulk deletes and raw SQL obey it too:

| `on_delete` | Deleting the target row… |
|---|---|
| `"cascade"` | deletes the rows that link to it. |
| `"set_null"` | empties their link. The field must be `Target \| None`. |
| `"restrict"` | is refused while rows link to it (the API answers `409`). |

`ForeignKey(...)` also takes `default` (an id), `read_only`, `write_only` and `description`. The schemas hold `author_id: int`, and `Meta.*_exclude` names `"author_id"`.

### `ManyToMany`: many rows

`tags: list[Tag] = ManyToMany()` adds no column. fyro creates a join table **`blog_post_tags`** with `post_id` and `tag_id`, both the primary key and both `ON DELETE CASCADE`: deleting a post or a tag removes the link, never the other row.

The schemas hold **`tag_ids: list[int]`**: the field name made singular plus `_ids` (`tags` is `tag_ids`, `categories` is `category_ids`, `staff` is `staff_ids`). `Create` defaults it to `[]`; on `Update` it replaces the whole set. Ids come back sorted.

### Rules

- The target must be a model that is already defined (imported). A model cannot link to itself or to a model defined later in the same file yet.
- A model as a field type needs `ForeignKey(...)`; `list[Model]` needs `ManyToMany()`. Leaving either out is an error naming the field.
- `author_id` or `tag_ids` declared next to the link is a name clash, also an error.

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
