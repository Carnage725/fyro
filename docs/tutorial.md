# Tutorial: from an empty folder to an API and an admin

This takes about 10 minutes. You need [uv](https://docs.astral.sh/uv/) (or pip) and Docker.

## 1. Install fyro and create a project

```bash
mkdir fyro-tutorial && cd fyro-tutorial
uv venv && source .venv/bin/activate
uv pip install fyro

fyro new mysite
cd mysite
```

`fyro new` writes:

```
mysite/
  manage.py           # runs fyro's commands for this project
  asgi.py             # the app: fyro.create_app()
  .env                # development settings (never commit it)
  .gitignore
  compose.yaml        # Postgres for this project
  mysite/
    settings.py
```

## 2. Start Postgres

```bash
docker compose up -d --wait
```

!!! tip "Port 5432 already taken?"
    Run `FYRO_DB_PORT=5433 docker compose up -d --wait` and change the port in `FYRO_DATABASE_URL` in `.env` to `5433`.

## 3. Create an app with a model

```bash
python manage.py startapp blog
```

Write `blog/models.py`:

```python
from fyro import Field, Model


class Post(Model):
    title: str = Field(max_length=200)
    body: str
    published: bool = False
```

Add the app (and fyro's built-in auth) to `mysite/settings.py`:

```python
from fyro import Settings


class ProjectSettings(Settings):
    installed_apps: list[str] = ["fyro.auth", "blog"]
```

## 4. Create the tables

```bash
python manage.py makemigrations
python manage.py migrate
```

`makemigrations` writes a file to `migrations/`; `migrate` applies it.

## 5. Create an admin user and run the server

```bash
python manage.py createsuperuser
python manage.py runserver
```

Open:

- **<http://127.0.0.1:8000/docs>**: the API, with every endpoint for `Post`.
- **<http://127.0.0.1:8000/admin>**: log in with your superuser and manage posts and users.

## 6. Use the API

Reading is public; writing needs a login (the default once `fyro.auth` is installed):

```bash
curl -s http://127.0.0.1:8000/api/blog/post
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"username": "admin", "password": "your-password"}' | python -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -s -X POST http://127.0.0.1:8000/api/blog/post \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"title": "Hello", "body": "First post"}'
```

## 7. Change the model

Add a field, then migrate again:

```python
    views: int = 0
```

```bash
python manage.py makemigrations
python manage.py migrate
```

Existing posts get `views = 0`. Next: the [guides](guides/models.md).
