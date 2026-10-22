# Security

## Before going to production

- Set `FYRO_DEBUG=false` (or leave it unset: `False` is the default).
- Set a real `FYRO_SECRET_KEY` (50+ random characters) in the environment. With `debug` off, fyro refuses the generated `fyro-insecure-…` key.
- Serve over **HTTPS**: outside `debug` the session cookie is `__Host-fyro_session` and `Secure`, so browsers only send it over HTTPS.
- Do not deploy the `.env` from `fyro new`: it is for development only and listed in `.gitignore`.
- Put a rate limiter in front of `/api/auth/login` (fyro has none, see below).

## What fyro does for you

| Area | Behavior |
|---|---|
| Passwords | Argon2id (`pwdlib`, m=64 MiB, t=3, p=4, above OWASP's minimum). Never returned by any endpoint or shown in the admin. |
| Login | The same `401` for an unknown user, a wrong password and an inactive user; unknown users still pay for a hash, so timing does not reveal usernames. |
| Sessions | 256-bit random tokens, stored only as SHA-256 hashes; a new token at every login; 14-day lifetime and 2-hour idle timeout enforced on the server; logout, password change and deactivation end sessions immediately. |
| Cookies | `HttpOnly`, `SameSite=Lax`; `__Host-` prefix and `Secure` outside `debug`. |
| CSRF | Cookie-authenticated writes must carry `X-Requested-With` or `HX-Request` (a custom header browsers cannot add cross-site). |
| Permissions | Checked before the row is looked up: no probing which ids exist. |
| Input | Validated by the generated schemas; values the database cannot store are a `422`, never a `500`. |
| SQL | Built by SQLAlchemy with bound parameters; field names are checked against the model before reaching SQL. |
| Admin | Superusers only; strict CSP, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`; HTML-escaped values; no third-party scripts or fonts. |
| Errors | The CLI masks the database password in error messages. |

## Known limits

- No rate limiting or account lockout.
- No per-row ownership checks until relations (v0.2): any logged-in user with write access can change any row of that model.
- The login response contains the token for API clients; JavaScript on the page can read it at that moment (it matters only if the site has an XSS hole, which defeats CSRF protection anyway).
- No password reset, password change endpoint or email verification.

Found a vulnerability? Please report it privately to the maintainer instead of opening a public issue.
