# Changelog

## Unreleased

### Links between models (v0.2)

- **`ForeignKey`:** `author: User = ForeignKey(on_delete="cascade")` adds an indexed `author_id` column with an `ON DELETE` rule (`cascade`, `set_null`, `restrict`) enforced by Postgres.
- **`ManyToMany`:** `tags: list[Tag] = ManyToMany()` adds a join table and `tag_ids` to the schemas.
- **Querying:** `select_related()` (a join) and `prefetch_related()` (one extra query). Reading a link that was not loaded raises `fyro.db.NotLoaded`, naming the call to add. `create`/`update` accept rows or ids for links; a `ManyToMany` value replaces the set.
- **Lookups:** `__exact`, `__gt`, `__gte`, `__lt`, `__lte`, `__in`, `__isnull`, `__icontains`, one hop across a `ForeignKey` (`author__name__icontains=`), and `tags=` / `tags__in=`.
- **Automatic API:** links as ids in bodies, `?expand=author,tags` (one level, target's `Read` schema and read permission, never a model hidden from the API), typed lookup parameters on the list endpoint (`?views__gte=10`, `?id__in=1&id__in=3`, `?tags=2`). A link to a missing row is a `422` naming the field; deleting a row still linked by `restrict` is a `409`.
- **Migrations:** linked tables are created in order; a required link on a table with rows needs `default=<id>`; turning a plain id column into a link applies `on_delete` to rows pointing at missing rows first. `makemigrations` asks whether a dropped and an added column is a rename, and keeps the data on yes (`--noinput` never asks).
- **Admin:** dropdowns and multi-selects for links, with a search box over 100 rows; list cells show the linked row's `__str__` and filter by it. Bulk delete is now one query.
- **Escape hatches:** `fyro.db.DBSession` (a FastAPI dependency) and `fyro.db.current_session()` give the request's SQLAlchemy session. New guide: Dropping down.
- **Breaking:** `expand` is a reserved list parameter, so a model field named `expand` is a startup error.
- **Upgrade note for `fyro.auth`:** `Session.user_id` is now a `ForeignKey` to `User`. Run `makemigrations` and `migrate`: sessions of users that no longer exist are deleted, then the constraint is added. Deleting a user now deletes their sessions, and an authenticated request needs one query instead of two.

### Also

- **Faster counts:** `count()` no longer sorts before counting, so lists with `?order_by=` on a field without an index count much faster (4.5× more list requests per second on 50,000 rows). The admin's list pages get the same speed-up.
- **`?with_total=false`** on the automatic list endpoint skips the count query and returns `"total": null`.
- **Breaking:** `with_total` is now a reserved list parameter, so a model field named `with_total` is a startup error (rename it or set `Meta.api = False`).

## 0.1.0 (2026-10-22)

First release.

- **Settings:** one `fyro.Settings` subclass per project, overridable from `FYRO_*` environment variables and `.env`; required secret key, refused outside debug when insecure.
- **Apps:** `installed_apps` loader with required `models.py` and optional `routes.py`, mounted under `/api/<app>`.
- **Models:** one class gives the table, `Create`/`Read`/`Update` schemas, `read_only`/`write_only` fields and `Meta` options. Input the database cannot store is rejected with a 422.
- **Querying:** async `Model.objects` manager (`filter`, `exclude`, `search`, `order_by`, `limit`, `offset`, `all`, `first`, `get`, `count`, `create`, `update`, `delete`) with a request-scoped session that commits before the response.
- **Automatic API:** list (filters, search, ordering, paging), get, create, patch and delete for every model, with 404, 409 and 422 handling.
- **Migrations:** `makemigrations` and `migrate` over Alembic with no config files; migrations never fail on tables that already have rows.
- **Command line:** `fyro new`, `startapp`, `makemigrations`, `migrate`, `runserver`, `createsuperuser`.
- **Auth:** `fyro.auth` with Argon2id passwords, database-backed session tokens (cookie or Bearer), CSRF header check, per-model permissions.
- **Admin:** `/admin` for superusers with search, sorting, filters, add/edit/delete, bulk delete and user management.
