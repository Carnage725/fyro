# What v0.2 does not do

fyro v0.2 is deliberately small. Not included yet:

- **Self-referencing and forward links**: a model cannot link to itself (`parent: Category`) or to a model defined later.
- **One-to-one links**, reverse access (`user.posts`; use `Post.objects.filter(author=user)`), expanding more than one level (`?expand=author.profile`) and lookups across more than one `ForeignKey`.
- **Lookups across a `ForeignKey` in the API** (`?author__name=`): Python-only, on purpose (see [Security](security.md)).
- **Databases other than PostgreSQL**, and multiple databases.
- **Per-row ownership permissions** (only the author may edit their post).
- **Model inheritance** and a custom user model.
- **Sign-up, password reset, password change endpoints, email, OAuth**, rate limiting.
- **Background tasks, signals, WebSockets.**
- **Data migration** helpers and squashing; a renamed model shows up as a dropped and a created table (renamed fields are detected).
- **An admin without JavaScript** (writes go through htmx), inline editing, history, CSV import/export, file uploads.

Real-world feedback decides what comes next.
