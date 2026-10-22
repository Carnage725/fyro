# Auth and permissions

Add `"fyro.auth"` to `installed_apps`, then `makemigrations` and `migrate`. You get a `User` model, login sessions, permissions on the automatic API, `createsuperuser` and the [admin](admin.md).

## Login

| Method and path | Input | Output |
|---|---|---|
| `POST /api/auth/login` | JSON `{"username", "password"}` | `200 {"token", "token_type": "bearer", "expires_at"}` plus a session cookie; `401 {"detail": "Invalid username or password."}` |
| `POST /api/auth/logout` | cookie or token | `204`; the session is deleted |
| `GET /api/auth/me` | cookie or token | `200` the user (never the password), or `401` |

- Passwords are hashed with **Argon2id**.
- A session is a random token stored in the database only as a SHA-256 hash. Browsers use the cookie; API clients send `Authorization: Bearer <token>`.
- Sessions end after **14 days**, or after **2 hours without use**. Logout, a password change or deactivating the user ends them immediately.
- The cookie is `HttpOnly` and `SameSite=Lax`; outside `debug` it is named `__Host-fyro_session` and is `Secure` (HTTPS only).
- An unknown user, a wrong password and an inactive user all get the same `401`, in the same time.

## CSRF

A request authenticated by the **cookie** that changes data (`POST`, `PUT`, `PATCH`, `DELETE`) must send an `X-Requested-With` or `HX-Request` header, or it gets `403`. Bearer-token requests need no header.

## The current user in your routes

```python
from typing import Annotated

from fastapi import Depends

from fyro.auth import User, current_user, optional_user


@router.get("/mine")
async def mine(user: Annotated[User, Depends(current_user)]):          # 401 if not logged in
    ...


@router.get("/maybe")
async def maybe(user: Annotated[User | None, Depends(optional_user)]):  # None if not logged in
    ...
```

## Permissions on the automatic API

```python
class Post(Model):
    ...

    class Meta:
        permissions = {"read": "public", "write": "superuser"}
```

- Actions: `read` (list, get) and `write` (create, patch, delete). Levels: `public`, `authenticated`, `superuser`.
- Default with `fyro.auth` installed: **read public, write authenticated**. Without `fyro.auth`, everything is public (fyro logs a warning when `debug` is off), and `Meta.permissions` is an error.
- Not logged in: `401`. Logged in but not allowed: `403`.
- The permission check runs **before** the row is looked up, so a caller who may not see a model gets `401`/`403` even for ids that do not exist; nobody can probe which ids exist.

!!! warning "No ownership checks yet"
    "authenticated" means any logged-in user may change any row. Per-row ownership needs relations (v0.2). There is no public sign-up: users come from `createsuperuser` or the admin.

## Users

`User` has `username` (unique), `email` (optional), `is_active`, `is_superuser` and `date_joined`. Create superusers with `python manage.py createsuperuser`, and other users in the admin.
