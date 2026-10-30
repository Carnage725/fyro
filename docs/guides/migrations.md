# Migrations

```bash
python manage.py makemigrations          # write a migration for model changes
python manage.py makemigrations -m "add tags"
python manage.py migrate                 # apply everything
python manage.py migrate <revision>      # move up or down to a revision
python manage.py migrate zero            # unapply everything (empty database)
```

(Or from Python: `fyro.migrations.makemigrations()` and `fyro.migrations.migrate(target)`; both are synchronous.)

- Migration files live in `<project>/migrations/`, named `YYYY_MM_DD_HHMM-<revision>_<slug>.py`. There is no `alembic.ini` and no `env.py` to maintain: fyro keeps them internal.
- When nothing changed, no file is written.
- `makemigrations` needs the database to be at the latest migration first; run `migrate`.
- Two migrations made from the same parent (usually on two branches) are an error naming both; set one file's `down_revision` to the other's revision.

## Changes to tables that already have rows

`makemigrations` never writes a migration that would fail on existing data:

| Change | What the migration does |
|---|---|
| Add a required field with a constant default (`views: int = 0`) | Existing rows get the default; no database default is left behind. |
| Add a required field with `default_factory=uuid.uuid4`, `datetime.now` or `date.today` | Existing rows get `gen_random_uuid()`, `now()` or `CURRENT_DATE` (one value per row for UUIDs). |
| Make an optional field required (`T \| None` to `T`) | Empty rows are filled from the default first. |
| Any of the above with no usable default, or a unique field with a constant default | Refused, with a message saying what to do. |
| Add an optional field | A plain column add. |
| Add a required `ForeignKey` | Needs `ForeignKey(..., default=<id of an existing row>)`: existing rows get that id, and the default can be removed after migrating. Without one it is refused; or make the link optional (`Target \| None`). |
| Turn a plain id column into a link (`author_id: int` into `author: Author = ForeignKey(...)`) | Rows pointing at a missing row get the `on_delete` treatment first: `cascade` deletes them, `set_null` empties the link, `restrict` leaves them (and the migration fails if there are any). `makemigrations` warns, and the SQL is in the file. |

Linked tables are created target first. Changing `on_delete` drops and recreates the constraint. A `ManyToMany` creates and drops its join table with the field.

## Renames

When a table loses a column and gains one of the same type and nullability, `makemigrations` asks:

```
Was blog_post.author renamed to writer? [y/N]
```

- **Yes** writes a rename (`ALTER TABLE … RENAME COLUMN`): the data is kept, and migrating back renames it again.
- **No** writes *drop the old column, add the new one*, which loses that column's data, and logs a warning.
- With `--noinput`, or without a terminal (CI), it never asks and always drops and adds.

From Python, pass `makemigrations(ask_rename=fn)`, where `fn(table, old, new)` returns `True` for a rename.

Only column renames are detected. A renamed model (table) still shows up as a dropped and a created table: edit the migration if you meant a rename.
