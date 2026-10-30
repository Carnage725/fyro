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
- `filter(x="a")` is equality; `None` means `IS NULL`. Several keywords must all match.
- `exclude(...)` keeps rows where the column is `NULL`, like Django: `exclude(price__gte=2)` keeps rows with no price.
- `order_by("-field")` sorts descending. `first()` orders by the primary key when unordered.
- `get()` raises `Post.DoesNotExist` or `Post.MultipleObjectsReturned` (subclasses of `fyro.db.DoesNotExist` / `fyro.db.MultipleObjectsReturned`). In a route, an uncaught `DoesNotExist` becomes a 404.
- `update()`/`delete()` cannot follow `order_by`, `limit` or `offset`.
- An unknown field name is a `TypeError` naming the model, the field and the valid fields.

## Lookups

Add `__<lookup>` to a field name:

| Lookup | Example | SQL |
|---|---|---|
| `exact` (the default) | `title="Hi"` | `title = 'Hi'` |
| `gt`, `gte`, `lt`, `lte` | `views__gte=10` | `views >= 10` |
| `in` | `id__in=[1, 2]` | `id IN (1, 2)` (an empty list matches nothing) |
| `isnull` | `subtitle__isnull=True` | `subtitle IS NULL` |
| `icontains` | `title__icontains="fyro"` | case-insensitive contains; `%` and `_` match literally |

An unknown lookup is a `TypeError` listing the valid ones; `icontains` on a non-text field is one too.

## Links

### Filtering

```python
await Post.objects.filter(author=ana).all()                 # a row or an id
await Post.objects.filter(author_id=5).all()
await Post.objects.filter(author__in=[ana, bob]).all()
await Post.objects.filter(editor__isnull=True).all()
await Post.objects.filter(author__username="ana").all()     # one hop across a ForeignKey
await Post.objects.filter(author__username__icontains="an").all()
await Post.objects.filter(tags=news).all()                  # ManyToMany: tags= or tags__in=
await Post.objects.exclude(tags__in=[news, tech]).all()
```

Lookups cross one `ForeignKey` at most (`author__username`, not `author__profile__city`). A `ManyToMany` takes `tags=` (a row or an id) or `tags__in=`.

### Loading linked rows

Linked rows are never loaded behind your back. Ask for them:

```python
post = await Post.objects.select_related("author").get(id=1)    # ForeignKey: a join, same query
post.author.username

posts = await Post.objects.prefetch_related("tags").all()        # ManyToMany: one extra query
[t.name for t in posts[0].tags]
posts[0].tag_ids                                                 # [1, 2]
```

`post.author_id` is always there. Reading `post.author`, `post.tags` or `post.tag_ids` without loading them raises `fyro.db.NotLoaded`, naming the call to add:

```
fyro.db.NotLoaded: Post.author is not loaded. Use Post.objects.select_related('author').
```

### Writing links

```python
post = await Post.objects.create(title="Hi", author=ana, tags=[news, tech])
post = await Post.objects.create(title="Hi", author_id=5, tag_ids=[1, 2])   # ids work too
await Post.objects.filter(id=1).update(author=bob, tags=[news])             # replaces the set
await Post.objects.filter(id=1).update(tag_ids=[])                          # clears it
```

`update()` with a `ManyToMany` replaces the whole set of every matching row; the rows are picked before the update runs, so `filter(title="a").update(title="b", tags=[...])` still finds them. Passing a row of the wrong model is a `TypeError`.

## Sessions

Inside a request there is nothing to set up: fyro opens a session per request, **commits just before the response** when the status is below 400, and rolls everything back on a 4xx/5xx or an exception. A failed commit becomes a 500, never a 200 for unsaved data.

In scripts and tests, open a session yourself:

```python
import fyro.db

async with fyro.db.session():      # commits on exit, rolls back on an exception
    await Post.objects.create(...)
```

Writes made while streaming a response body, after the response has started, are not committed.

For queries the manager cannot express, use SQLAlchemy on the same session: see [Dropping down](dropping-down.md).
