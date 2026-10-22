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

## Renames

Alembic cannot detect renames: renaming a field shows up as *drop the old column, add the new one*, which loses that column's data. `makemigrations` logs a warning when one migration drops and adds columns on the same table. Edit the generated migration if you meant a rename.
