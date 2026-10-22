# fyro

**Fast, Yet Ridiculously Opinionated.** A web framework on top of [FastAPI](https://fastapi.tiangolo.com/) that feels like Django.

FastAPI is the engine. fyro adds what a project needs around it, with one obvious place for everything:

- **One model class** gives you a Postgres table, `Create`/`Read`/`Update` schemas, a full REST API with docs, and an admin page.
- **No database plumbing:** `await Post.objects.filter(published=True).all()` works in any route; fyro opens, commits and rolls back the session for you.
- **Migrations** with `makemigrations` and `migrate`, built on Alembic, with no config files to write.
- **A command line** (`fyro new`, `startapp`, `runserver`, …) and a generated project layout.
- **Auth** (users, login sessions, permissions) and an **admin panel**, both built in.

```python
# blog/models.py
from fyro import Field, Model


class Post(Model):
    title: str = Field(max_length=200)
    body: str
    secret: str = Field(write_only=True)
    published: bool = False
```

That class is the table `blog_post`, the API at `/api/blog/post` (list, get, create, patch, delete, with filters, search and paging), and the admin page at `/admin/blog/post`.

!!! note "Status"
    v0.1, alpha. Postgres only. Relations between models come in v0.2. See [What v0.1 does not do](limitations.md).

## Install

```bash
uv pip install fyro      # or: pip install fyro
```

fyro needs Python 3.12 or newer and PostgreSQL 16 or newer. Then follow the [10-minute tutorial](tutorial.md).
