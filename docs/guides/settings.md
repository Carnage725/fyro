# Settings

A project has one settings class, in `<project>/settings.py`:

```python
from fyro import Settings


class ProjectSettings(Settings):
    installed_apps: list[str] = ["fyro.auth", "blog"]
```

The module must define exactly one subclass of `fyro.Settings`. fyro finds it through the `FYRO_SETTINGS` environment variable (`manage.py` and `asgi.py` set it for you, e.g. `mysite.settings`).

## Fields

| Field | Default | Notes |
|---|---|---|
| `database_url` | required | Must use `postgresql+asyncpg://`. |
| `secret_key` | required | See below. |
| `debug` | `False` | Development mode. |
| `installed_apps` | `[]` | Dotted names of your apps, e.g. `"blog"` or `"fyro.auth"`. |

Add your own fields to the class like any Pydantic model.

## Environment variables and `.env`

Every field can be set as `FYRO_<FIELD>` in the environment or in a `.env` file in the working directory. The environment wins over `.env`, which wins over `settings.py`:

```bash
FYRO_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/mysite
FYRO_DEBUG=true
FYRO_INSTALLED_APPS='["fyro.auth", "blog"]'   # JSON
FYRO_INSTALLED_APPS=fyro.auth,blog              # or comma-separated
```

`.env` may only contain settings fields: an unknown key is an error, so a typo cannot be silently ignored.

## The secret key

`fyro new` writes a random `FYRO_SECRET_KEY=fyro-insecure-…` to `.env`. A key is *insecure* if it starts with `fyro-insecure-` or is shorter than 50 characters:

- with `debug` on, that is allowed (the CLI does not nag about it);
- with `debug` off, fyro refuses to start.

For production, generate a real key and set it in the environment:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(50))'
```

## Errors

Settings problems stop fyro with a message naming the field and the fix, for example `secret_key: Field required. Set FYRO_SECRET_KEY in the environment or .env`.
