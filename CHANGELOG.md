# Changelog

## 0.1.0 (unreleased)

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
