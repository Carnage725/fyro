# The automatic API

Every model gets REST routes under `/api/<app>/<model>`, documented at `/docs`. For `Post` in `blog`:

| Method and path | Input | Output |
|---|---|---|
| `GET /api/blog/post` | query: filters, `search`, `order_by`, `limit` (default 50, max 100), `offset`, `with_total`, `expand` | `200 {"items": [...], "total": n, "limit": 50, "offset": 0}` |
| `GET /api/blog/post/{id}` | query: `expand` | `200` the post, or `404` |
| `POST /api/blog/post` | body: `Post.Create` | `201` the post, `409` on a duplicate unique value, `422` for a link to a missing row |
| `PATCH /api/blog/post/{id}` | body: `Post.Update` (only the fields you send change) | `200`, `404`, `409` or `422` |
| `DELETE /api/blog/post/{id}` | | `204`, `404`, or `409` while `on_delete="restrict"` links point at it |

- The path segment is the class name in snake_case (`BlogPost` is `/api/blog/blog_post`). The id parameter is named after the primary key field (`{id}`, or `{slug}` for a `slug` primary key).
- **Filters:** one optional query parameter per readable field, typed: `?published=true&status=live`. Each also has [lookups](querying.md#lookups), typed to the field: `?views__gte=10&views__lt=100` (numbers and dates), `?title__icontains=fyro` (text), `?subtitle__isnull=true` (optional fields), `?id__in=1&id__in=3` (repeat the parameter). A `ManyToMany` adds `?tags=2` and `?tags__in=1&tags__in=2`.
- **Search:** `?search=text` is a case-insensitive *contains* over `Meta.search_fields` (default: every readable text field). `%` and `_` match literally.
- **Ordering:** `?order_by=title` or `?order_by=-title`, on any readable field.
- **Total:** `total` counts every matching row, which gets slow on large tables. `?with_total=false` skips the count and returns `"total": null`.
- Responses always go through `Post.Read`: `write_only` fields are never returned and cannot be filtered on.
- Bad input is a `422`; input the database could not store (a NUL character, an integer out of range) is also a `422`, never a `500`.

Fields named `limit`, `offset`, `search`, `order_by`, `with_total` or `expand` would clash with these parameters and are a startup error; rename them or set `Meta.api = False`.

## Links

A `ForeignKey` is its id in both directions; a `ManyToMany` is a list of ids, which replaces the whole set on `PATCH`:

```
POST /api/blog/post   {"title": "Hi", "author_id": 5, "tag_ids": [1, 2]}
201                   {"id": 1, "title": "Hi", "author_id": 5, "editor_id": null, "tag_ids": [1, 2]}

POST /api/blog/post   {"title": "Bad", "author_id": 99}
422                   {"detail": "author_id: no Author with id 99."}
```

`?expand=` adds the linked rows, one level deep, on list and get:

```
GET /api/blog/post/1?expand=author,tags
{"id": 1, "title": "Hi", "author_id": 5, "editor_id": null, "tag_ids": [1, 2],
 "author": {"id": 5, "name": "ana"},
 "tags": [{"id": 1, "name": "news"}, {"id": 2, "name": "tech"}]}
```

- Expanded rows use the target's own `Read` schema, so its `write_only` fields never appear.
- The caller needs the target's read permission (`401`/`403` otherwise).
- A target hidden from the API (`Meta.api = False`, like `fyro.auth`'s `User`) cannot be expanded: `422`. Its id is still returned.
- Cost: a page with expanded `ForeignKey`s is one query (a join). Each `ManyToMany` adds one query per page, not per row, and is always loaded because `tag_ids` is part of every row.
- Lookups across a `ForeignKey` (`author__name`) are Python-only: on the API they would let anyone probe the target's fields, one guess at a time.

## Your own routes

Add `<app>/routes.py` with a router; it is mounted at `/api/<app>` before the automatic routes, so `/api/blog/post/stats` is not mistaken for an id:

```python
from fastapi import APIRouter

from blog.models import Post

router = APIRouter()


@router.get("/post/stats")
async def stats():
    return {"posts": await Post.objects.count()}
```

Two routes with the same method and path (yours or automatic) are a startup error naming both. Turn off the automatic routes for one model with `Meta.api = False`.

Permissions for the automatic routes are in [Auth and permissions](auth.md).
