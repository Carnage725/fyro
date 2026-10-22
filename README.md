# fyro

**Fast, Yet Ridiculously Opinionated.** A web framework on top of FastAPI that feels like Django.

FastAPI is the engine. fyro adds what a project needs around it, with one obvious place for everything: one model class gives you a Postgres table, `Create`/`Read`/`Update` schemas, a REST API with docs, migrations and an admin page. Database sessions, a command line, users, login and permissions are built in.

```python
# blog/models.py
from fyro import Field, Model


class Post(Model):
    title: str = Field(max_length=200)
    body: str
    secret: str = Field(write_only=True)
    published: bool = False
```

That class is the table `blog_post`, the API at `/api/blog/post` (list with filters, search, ordering and paging; get; create; patch; delete) and the admin page at `/admin/blog/post`. Queries need no session code:

```python
posts = await Post.objects.filter(published=True).order_by("-id").limit(10).all()
```

## Quickstart

Needs Python 3.12+, Docker (for Postgres) and [uv](https://docs.astral.sh/uv/) or pip.

```bash
uv venv && source .venv/bin/activate
uv pip install fyro

fyro new mysite && cd mysite
docker compose up -d --wait
python manage.py startapp blog
# write blog/models.py, then in mysite/settings.py:
#     installed_apps: list[str] = ["fyro.auth", "blog"]
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver        # http://127.0.0.1:8000/docs and /admin
```

The full walkthrough and the guides are in [`docs/`](docs/index.md): settings, models, querying, the automatic API, migrations, the command line, auth and permissions, the admin, and security notes.

## Status

**v0.1, alpha.** PostgreSQL only. Relations between models come in v0.2; see [what v0.1 does not do](docs/limitations.md). The plan and the history of every design decision are in [`PLAN.md`](PLAN.md) and [`BUILD_PROMPT.md`](BUILD_PROMPT.md).

## Development

```bash
uv sync                     # install dependencies
docker compose up -d        # Postgres 16 on localhost:5432 (user, password, db: fyro)
uv run pytest               # tests, in random order (pytest-randomly)
uv run ruff check           # lint
uv run --only-group docs zensical serve   # docs at http://127.0.0.1:8000
```

How tests are written and attacked is described in [`test.md`](test.md).

## License

[MIT](LICENSE). The admin bundles [htmx](https://htmx.org) (0BSD) and [JetBrains Mono](https://www.jetbrains.com/lp/mono/) (OFL-1.1); their licenses are in `src/fyro/admin/static/`.
