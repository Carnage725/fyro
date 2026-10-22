"""The admin: /admin pages for superusers, mounted by create_app() when fyro.auth is installed."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fyro.admin.routes import AdminResponse, router

STATIC_DIR = Path(__file__).parent / "static"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


class AdminSecurityHeaders:
    """Adds the admin's security headers to every /admin response (pure ASGI)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/admin"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def _admin_response(request: Request, exc: Exception) -> object:
    return exc.response  # type: ignore[attr-defined]


def mount(app: FastAPI) -> None:
    # Static first: /admin/static/... must not match the /admin/{app}/{model} page route.
    app.mount("/admin/static", StaticFiles(directory=STATIC_DIR), name="admin-static")
    app.include_router(router)
    app.add_exception_handler(AdminResponse, _admin_response)
    app.add_middleware(AdminSecurityHeaders)
