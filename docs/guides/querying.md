# Querying

Every model has a manager at `Model.objects`. All methods that touch the database are `async`.

```python
post = await Post.objects.create(title="Hi", slug="hi", body="…", secret="s")
posts = await Post.objects.filter(published=True).order_by("-id").limit(10).offset(20).all()
first = await Post.objects.exclude(title="x").first()          # Post | None
one = await Post.objects.get(id=1)                              # or raises
n = await Post.objects.filter(published=False).count()
changed = await Post.objects.filter(id=1).update(title="New")   # rows updated
removed = await Post.objects.filter(published=False).delete()   # rows deleted
found = await Post.objects.search("fyro", ["title", "body"]).all()  # case-insensitive contains
```

- Querysets are immutable: every chained call returns a new one.
- `filter`/`exclude` take keyword **equality** only. `None` means `IS NULL`. `exclude(x="a")` keeps rows where `x` is `NULL`, like Django.
- `order_by("-field")` sorts descending. `first()` orders by the primary key when unordered.
- `get()` raises `Post.DoesNotExist` or `Post.MultipleObjectsReturned` (subclasses of `fyro.db.DoesNotExist` / `fyro.db.MultipleObjectsReturned`). In a route, an uncaught `DoesNotExist` becomes a 404.
- `update()`/`delete()` cannot follow `order_by`, `limit` or `offset`.
- An unknown field name is a `TypeError` naming the model, the field and the valid fields.

## Sessions

Inside a request there is nothing to set up: fyro opens a session per request, **commits just before the response** when the status is below 400, and rolls everything back on a 4xx/5xx or an exception. A failed commit becomes a 500, never a 200 for unsaved data.

In scripts and tests, open a session yourself:

```python
import fyro.db

async with fyro.db.session():      # commits on exit, rolls back on an exception
    await Post.objects.create(...)
```

Writes made while streaming a response body, after the response has started, are not committed.
