# Admin

With `fyro.auth` installed, the admin is at **`/admin`**, for superusers only.

- Every model appears automatically, grouped by app. Hide one with `Meta.admin = False`.
- Lists: search, click a column to sort, filters for yes/no and choice fields, 50 rows per page.
- Add, edit and delete, with validation errors shown next to each field and duplicate unique values reported on their field. Delete and bulk delete ask for confirmation.
- Users: create users (the password is hashed), set a new password, activate or deactivate, grant superuser. Changing a password or deactivating a user logs them out everywhere. You cannot delete, deactivate or remove superuser from yourself.
- Light and dark mode follow your system setting.

Customize per model:

```python
class Meta:
    list_display = ("id", "title", "published")
    search_fields = ("title",)
    admin = False
```

## Security

The admin uses the same login and session cookie as the API. It sends a strict `Content-Security-Policy` (`default-src 'self'; frame-ancestors 'none'`), `X-Frame-Options: DENY` and `Referrer-Policy: same-origin`; all values are HTML-escaped. Everything it needs (htmx and the JetBrains Mono font) ships inside the package: no CDN. Every change goes through htmx, which sends the CSRF header, so the admin needs JavaScript.
