# The automatic API

Every model gets REST routes under `/api/<app>/<model>`, documented at `/docs`. For `Post` in `blog`:

| Method and path | Input | Output |
|---|---|---|
| `GET /api/blog/post` | query: filters, `search`, `order_by`, `limit` (default 50, max 100), `offset`, `with_total` | `200 {"items": [...], "total": n, "limit": 50, "offset": 0}` |
| `GET /api/blog/post/{id}` | | `200` the post, or `404` |
| `POST /api/blog/post` | body: `Post.Create` | `201` the post, `409` on a duplicate unique value |
| `PATCH /api/blog/post/{id}` | body: `Post.Update` (only the fields you send change) | `200`, `404` or `409` |
| `DELETE /api/blog/post/{id}` | | `204` or `404` |

- The path segment is the class name in snake_case (`BlogPost` is `/api/blog/blog_post`). The id parameter is named after the primary key field (`{id}`, or `{slug}` for a `slug` primary key).
- **Filters:** one optional query parameter per readable field, typed, equality only: `?published=true&status=live`.
- **Search:** `?search=text` is a case-insensitive *contains* over `Meta.search_fields` (default: every readable text field). `%` and `_` match literally.
- **Ordering:** `?order_by=title` or `?order_by=-title`, on any readable field.
- **Total:** `total` counts every matching row, which gets slow on large tables. `?with_total=false` skips the count and returns `"total": null`.
- Responses always go through `Post.Read`: `write_only` fields are never returned and cannot be filtered on.
- Bad input is a `422`; input the database could not store (a NUL character, an integer out of range) is also a `422`, never a `500`.

Fields named `limit`, `offset`, `search`, `order_by` or `with_total` would clash with these parameters and are a startup error; rename them or set `Meta.api = False`.

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
