# Why fyro exists

## The short version

FastAPI is a great engine. Django is a great car. fyro puts the FastAPI engine inside a Django-style car.

You write one small class. fyro turns it into a database table, an API with docs, migrations and an admin page. You don't write glue code, and you don't have to decide where files go.

## The problem

Imagine two ways to get a new bookshelf.

- **FastAPI gives you good wood and no instructions.** You can build anything, but every project starts with the same two days of work: connecting to the database, opening and closing database sessions, settings, migrations, login, folder layout, and an admin screen you build yourself. Every team does it differently, so no two FastAPI projects look alike.
- **Django gives you flat-pack furniture with numbered steps.** A new developer knows where everything lives within five minutes. But Django was born before Python had `async` and type hints. You also describe the same fields three times: once in the model, again in the serializer and again in the view.

fyro aims for the middle: Django's "one obvious place for everything", with FastAPI's types, async and automatic docs.

## One class does the work of four

In plain FastAPI, a blog post usually needs four classes that you keep in sync by hand:

```python
class Post(Base):            # the database table (SQLAlchemy)
    ...
class PostCreate(BaseModel): # what a client may send
    ...
class PostRead(BaseModel):   # what a client gets back
    ...
class PostUpdate(BaseModel): # what a client may change
    ...
```

Then you write five routes (list, get, create, update, delete), a `get_db` function and a migration.

In fyro you write this:

```python
from fyro import Field, Model


class Post(Model):
    title: str = Field(max_length=200)
    body: str
    secret: str = Field(write_only=True)
    published: bool = False
```

From that one class you get:

| You get | Where |
|---|---|
| A Postgres table | `blog_post` |
| Three checked schemas | `Post.Create`, `Post.Read`, `Post.Update` |
| A REST API with filters, search, ordering and paging | `/api/blog/post` |
| Interactive API docs | `/docs` |
| A migration | `python manage.py makemigrations` |
| An admin page | `/admin/blog/post` |
| Queries with no session code | `await Post.objects.filter(published=True).all()` |

Small flags decide what goes where. `write_only=True` means "accept it, never send it back", which suits a password or a secret. `read_only=True` means "send it back, never accept it", which suits an id or a view counter.

## How fyro combines Django, FastAPI and SQLAlchemy

fyro does not reinvent the hard parts. It wraps tools that already work, and adds the parts that decide how a project fits together.

| Piece | Who does the work | What fyro adds |
|---|---|---|
| Web requests, routing, API docs | **FastAPI** (with Starlette and Uvicorn) | Builds the routes for you from each model |
| Checking input and output | **Pydantic** | Builds the `Create`, `Read` and `Update` schemas from the model's flags |
| Talking to the database | **SQLAlchemy** (async) with **asyncpg** | Hides the engine and the session; one session per request, committed or rolled back for you |
| Migrations | **Alembic** | `makemigrations` and `migrate`, with no config files |
| Project layout, apps, settings, command line | inspired by **Django** | `fyro new`, `startapp`, `installed_apps`, `manage.py` |
| Users, login, admin | built into fyro | `fyro.auth`, and an admin made with htmx |

A simple way to picture it: FastAPI is the kitchen, SQLAlchemy is the pantry, and Pydantic checks every order. fyro is the restaurant manager who sets up the kitchen the same way every time, so any cook can walk in and start working.

### What happens when the app starts

When the app starts, fyro reads your settings, imports each installed app's `models.py` and `routes.py`, and builds the tables, schemas and routes once. After that, each request follows the same short path: open a session, run your query, check the output, commit, reply. The clever work happens once at startup, and the work done on every request stays small.

## fyro compared with SQLModel

[SQLModel](https://sqlmodel.tiangolo.com/) is a great library, and it inspired part of fyro. It lets one class be both a SQLAlchemy table and a Pydantic model. But its own tutorial still has you write `HeroCreate`, `HeroPublic` and `HeroUpdate` yourself, sharing fields through a base class, because the input and output shapes differ. SQLModel is a model library. It is not a framework, and it doesn't try to be one.

| | SQLModel | fyro |
|---|---|---|
| One class is the table and the Pydantic model | Yes | Yes |
| `Create`/`Read`/`Update` schemas | You write them (with inheritance) | Built for you from `read_only` and `write_only` flags |
| REST endpoints | You write them | Built for you, with filters, search, ordering and paging |
| Database session | You write `get_db` and pass a `Session` around | Opened, committed and rolled back for you per request |
| Migrations | Set up Alembic yourself | `makemigrations` and `migrate` |
| Project layout and command line | None | `fyro new`, `startapp`, `runserver`, and more |
| Users, login, permissions | None | `fyro.auth` |
| Admin panel | None | Built in |
| Safety checks | Pydantic validation | Also rejects input Postgres cannot store (such as NUL characters or out-of-range integers) with a 422 instead of a crash, and refuses migrations that would fail on tables that already have rows |

fyro doesn't build on top of SQLModel. It uses Pydantic and SQLAlchemy directly and owns the layer that generates everything, because that layer is the product.

## What fyro gives up

Being opinionated means fyro says "no" to some things, on purpose:

- **Postgres only.** No MySQL or SQLite for now.
- **No relations between models yet.** Foreign keys come in v0.2.
- **One way to do things.** If you want to choose every piece yourself, plain FastAPI suits you better.
- **A little speed.** fyro does extra checking on every request. In the [benchmarks](benchmarks.md) this cost sits within the noise of the measurements, and both async stacks served single-row reads faster than Django and Flask.

The full list is in [What v0.1 does not do](limitations.md).

## Who fyro is for

- You like Django's structure but want async, type hints and FastAPI's automatic docs.
- You are tired of writing the same four classes and five routes for every table.
- You want a working API and admin in ten minutes, and the freedom to override any piece later.

## Thanks

fyro stands on the work of the people behind FastAPI, Starlette, Pydantic, SQLAlchemy, Alembic, asyncpg, Uvicorn, Django, SQLModel and htmx. fyro is the glue; they built the parts.
