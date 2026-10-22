# What v0.1 does not do

fyro v0.1 is deliberately small. Not included yet:

- **Relations** between models (foreign keys, many-to-many, relation pickers in the admin): v0.2.
- **Databases other than PostgreSQL**, and multiple databases.
- **Lookups beyond equality** in `filter` (`__gt`, `__in`, …), and per-row ownership permissions.
- **Model inheritance** and a custom user model.
- **Sign-up, password reset, password change endpoints, email, OAuth**, rate limiting.
- **Background tasks, signals, WebSockets.**
- **Data migrations** helpers and squashing; renames show up as drop + add.
- **An admin without JavaScript** (writes go through htmx), inline editing, history, CSV import/export, file uploads.

Real-world feedback decides what comes next.
