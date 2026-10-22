"""Server-rendered admin pages. Reads and writes go through the same query code, schemas and
manager as auto CRUD; HTMX (hx-boost) turns links and forms into partial page updates."""

import enum
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError

from fyro import apps
from fyro.admin import forms
from fyro.crud import list_query, unique_violation
from fyro.models.base import _snake, _unwrap_optional

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
PAGE_SIZE = 50
# Strict CSP: no eval, no inline styles or scripts. 409/422 bodies are swapped so forms can
# show their errors; other 4xx/5xx are not.
HTMX_CONFIG = json.dumps(
    {
        "includeIndicatorStyles": False,
        "allowEval": False,
        "allowScriptTags": False,
        "responseHandling": [
            {"code": "204", "swap": False},
            {"code": "[23]..", "swap": True},
            {"code": "(403|409|422)", "swap": True, "error": False},
            {"code": "[45]..", "swap": False, "error": True},
        ],
    }
)

router = APIRouter(prefix="/admin", include_in_schema=False)


class AdminResponse(Exception):
    """Raised by the access check to answer with a redirect or a page."""

    def __init__(self, response: Response) -> None:
        self.response = response


@dataclass
class Entry:
    model: Any
    app_label: str
    segment: str

    @property
    def url(self) -> str:
        return f"/admin/{self.app_label}/{self.segment}"

    @property
    def plural(self) -> str:
        return self.model.__name__ + "s"

    @property
    def is_user(self) -> bool:
        return self.model.__module__ == "fyro.auth.models" and self.model.__name__ == "User"


def entries() -> list[Entry]:
    return [
        Entry(model, app.label, _snake(model.__name__))
        for app in apps.registry.apps.values()
        for model in app.models.values()
        if model.__fyro_admin__
    ]


def find_entry(app_label: str, segment: str) -> Entry:
    for entry in entries():
        if (entry.app_label, entry.segment) == (app_label, segment):
            return entry
    raise HTTPException(404, "Not found.")


def render(request: Request, template: str, user: Any, status: int = 200, **context: Any) -> HTMLResponse:
    nav: dict[str, list[Entry]] = {}
    for entry in entries():
        nav.setdefault(entry.app_label, []).append(entry)
    return TEMPLATES.TemplateResponse(
        request,
        template,
        {"user": user, "nav": nav, "htmx_config": HTMX_CONFIG, **context},
        status_code=status,
    )


def redirect(url: str) -> Response:
    """HTMX follows HX-Redirect with a full page load."""
    return Response(status_code=200, headers={"HX-Redirect": url})


async def form_data(request: Request) -> dict[str, list[str]]:
    # parse_qs instead of request.form(): no python-multipart dependency for urlencoded forms
    return parse_qs((await request.body()).decode(), keep_blank_values=True)


async def superuser(request: Request) -> Any:
    """Access check: anonymous -> login page, non-superuser -> 403, writes need HX-Request."""
    from fyro.auth import optional_user

    user = await optional_user(request)  # raises 403 for cookie writes without the CSRF header
    if user is None:
        login = "/admin/login?next=" + quote(request.url.path, safe="")
        raise AdminResponse(Response(status_code=303, headers={"Location": login}))
    if not user.is_superuser:
        raise AdminResponse(render(request, "forbidden.html", None, status=403))
    return user


Superuser = Annotated[Any, Depends(superuser)]


def safe_next(url: str) -> str:
    """Only redirect within the admin after login (no open redirect)."""
    return url if url.startswith("/admin") and not url.startswith("//") else "/admin"


def parse_pk(entry: Entry, raw: str) -> Any:
    column = next(iter(entry.model.__table__.primary_key.columns))
    try:
        return column.type.python_type(raw)
    except (TypeError, ValueError) as error:
        raise HTTPException(404, "Not found.") from error


def pk_name(entry: Entry) -> str:
    return next(iter(entry.model.__table__.primary_key.columns)).key


async def get_row(entry: Entry, pk: Any) -> Any:
    row = await entry.model.objects.filter(**{pk_name(entry): pk}).first()
    if row is None:
        raise HTTPException(404, "Not found.")
    return row


# --- login / logout --------------------------------------------------------


@router.get("/login")
async def login_page(request: Request, next: str = "/admin") -> HTMLResponse:
    return render(request, "login.html", None, next=safe_next(next))


@router.post("/login")
async def login(request: Request) -> Response:
    from fyro.auth.sessions import authenticate, create_session, set_cookie

    data = await form_data(request)
    username, password = data.get("username", [""])[0], data.get("password", [""])[0]
    next_url = safe_next(data.get("next", ["/admin"])[0])
    user = await authenticate(username, password)
    if user is None:
        return render(
            request, "login.html", None, status=401, next=next_url, username=username,
            error="Invalid username or password.",
        )
    token, _ = await create_session(user)
    response = redirect(next_url)
    set_cookie(response, token)
    return response


@router.post("/logout")
async def logout(request: Request, user: Superuser) -> Response:
    from fyro.auth.sessions import clear_cookie, end_session

    await end_session(request)
    response = redirect("/admin/login")
    clear_cookie(response)
    return response


# --- pages -----------------------------------------------------------------


@router.get("")
async def index(request: Request, user: Superuser) -> HTMLResponse:
    counts = {entry.url: await entry.model.objects.count() for entry in entries()}
    return render(request, "index.html", user, counts=counts)


def filter_options(entry: Entry) -> list[tuple[str, list[tuple[str, str]]]]:
    """Sidebar filters: bool and Enum fields that are listed in the table's Read schema."""
    options = []
    for name in entry.model.Read.model_fields:
        if name not in entry.model.__fyro_fields__:
            continue
        tp, _ = _unwrap_optional(entry.model.Read.model_fields[name].annotation)
        if tp is bool:
            options.append((name, [("true", "Yes"), ("false", "No")]))
        elif isinstance(tp, type) and issubclass(tp, enum.Enum):
            options.append((name, [(str(m.value), m.name.title()) for m in tp]))
    return options


@router.get("/{app_label}/{segment}")
async def list_page(
    request: Request, app_label: str, segment: str, user: Superuser
) -> HTMLResponse:
    entry = find_entry(app_label, segment)
    params = request.query_params
    search, order_by = params.get("search", ""), params.get("order_by", "")
    page = max(int(params.get("page", "1")) if params.get("page", "").isdigit() else 1, 1)
    filters, active = {}, {}
    for name, choices in filter_options(entry):
        value = params.get(name)
        if value in {v for v, _ in choices}:
            active[name] = value
            filters[name] = value == "true" if choices[0][0] == "true" else value
    try:
        qs = list_query(entry.model, filters, search or None, order_by or None)
    except ValueError:
        order_by = ""
        qs = list_query(entry.model, filters, search or None)
    total = await qs.count()
    rows = await qs.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all()
    state = {"search": search, "order_by": order_by, **active}

    def url(**changes: Any) -> str:
        merged = {k: v for k, v in {**state, **changes}.items() if v not in ("", None)}
        if merged.get("page") == 1:
            del merged["page"]
        return entry.url + ("?" + urlencode(merged) if merged else "")

    columns = []
    for name in entry.model.__fyro_list_display__:
        flipped = name if order_by != name else f"-{name}"
        arrow = " ↑" if order_by == name else " ↓" if order_by == f"-{name}" else ""
        columns.append({"label": forms.label(name), "sort_url": url(order_by=flipped, page=1), "arrow": arrow})
    pk = pk_name(entry)
    table = [
        {"pk": getattr(row, pk), "cells": [forms.display(getattr(row, n)) for n in entry.model.__fyro_list_display__]}
        for row in rows
    ]
    pages = max(math.ceil(total / PAGE_SIZE), 1)
    return render(
        request, "list.html", user, entry=entry, columns=columns, rows=table, total=total,
        page=page, pages=pages, search=search, url=url, active=active,
        filters=filter_options(entry), keep=[(k, v) for k, v in state.items() if k != "search" and v],
        searchable=bool(entry.model.__fyro_search_fields__),
    )


@router.get("/{app_label}/{segment}/add")
async def add_page(
    request: Request, app_label: str, segment: str, user: Superuser
) -> HTMLResponse:
    entry = find_entry(app_label, segment)
    return render(
        request, "form.html", user, entry=entry,
        fields=add_fields(entry), action=f"{entry.url}/add", title=f"Add {entry.model.__name__}",
    )


def add_fields(entry: Entry, errors: dict[str, str] | None = None, form: Any = None) -> list[forms.FormField]:
    fields = forms.build_fields(entry.model, entry.model.Create)
    if entry.is_user:
        fields += [
            forms.FormField(name="password", label="Password", widget="password", required=True),
            forms.FormField(name="password2", label="Password (again)", widget="password", required=True),
        ]
    return with_errors(fields, errors or {}, form)


def edit_fields(entry: Entry, row: Any, errors: dict[str, str] | None = None, form: Any = None) -> list[forms.FormField]:
    fields = forms.build_fields(entry.model, entry.model.Update, row, editing=True)
    if entry.is_user:
        fields.append(
            forms.FormField(
                name="new_password", label="New password", widget="password",
                help="Leave blank to keep the current password. Changing it logs the user out everywhere.",
            )
        )
    return with_errors(fields, errors or {}, form)


def with_errors(fields: list[forms.FormField], errors: dict[str, str], form: Any) -> list[forms.FormField]:
    """Attach errors and keep what the user typed (except passwords)."""
    for form_field in fields:
        form_field.error = errors.get(form_field.name, "")
        if form is not None and not form_field.readonly and form_field.widget != "password":
            if form_field.widget == "checkbox":
                form_field.checked = form_field.name in form
            else:
                form_field.value = form.get(form_field.name, [""])[0]
    return fields


@router.post("/{app_label}/{segment}/add")
async def add(
    request: Request, app_label: str, segment: str, user: Superuser
) -> Response:
    entry = find_entry(app_label, segment)
    form = await form_data(request)
    values, errors = forms.parse(entry.model, entry.model.Create, form, editing=False)
    if entry.is_user and not errors:
        password, again = form.get("password", [""])[0], form.get("password2", [""])[0]
        if not password:
            errors["password"] = "This field is required."
        elif password != again:
            errors["password2"] = "Passwords do not match."
        else:
            from fyro.auth.passwords import hash_password

            values["password"] = hash_password(password)
    status = 422
    if not errors:
        try:
            await entry.model.objects.create(**values)
            return redirect(entry.url)
        except IntegrityError as error:
            status, errors = conflict_errors(entry, error)
    return render(
        request, "form.html", user, status=status, entry=entry,
        fields=add_fields(entry, errors, form), action=f"{entry.url}/add",
        title=f"Add {entry.model.__name__}", error=errors.get("__all__", ""),
    )


def conflict_errors(entry: Entry, error: IntegrityError) -> tuple[int, dict[str, str]]:
    columns, message = unique_violation(entry.model, error)
    return 409, {(columns[0] if len(columns) == 1 else "__all__"): message}


@router.post("/{app_label}/{segment}/bulk-delete")
async def bulk_delete(
    request: Request, app_label: str, segment: str, user: Superuser
) -> Response:
    entry = find_entry(app_label, segment)
    form = await form_data(request)
    pks = [parse_pk(entry, raw) for raw in form.get("ids", [])]
    rows = [row for pk in pks if (row := await entry.model.objects.filter(**{pk_name(entry): pk}).first())]
    if entry.is_user and user.id in pks:
        return confirm(request, user, entry, rows, status=422, error="You cannot delete yourself.")
    if form.get("confirm", [""])[0] != "yes" or not rows:
        return confirm(request, user, entry, rows)
    for row in rows:
        await entry.model.objects.filter(**{pk_name(entry): getattr(row, pk_name(entry))}).delete()
    return redirect(entry.url)


def confirm(request: Request, user: Any, entry: Entry, rows: list[Any], status: int = 200, error: str = "") -> HTMLResponse:
    pk = pk_name(entry)
    shown = entry.model.__fyro_list_display__
    return render(
        request, "confirm_delete.html", user, status=status, entry=entry, error=error,
        items=[" · ".join(forms.display(getattr(row, n)) for n in shown) for row in rows],
        ids=[getattr(row, pk) for row in rows],
        action=f"{entry.url}/bulk-delete",
    )


@router.get("/{app_label}/{segment}/{pk}")
async def edit_page(
    request: Request, app_label: str, segment: str, pk: str, user: Superuser
) -> HTMLResponse:
    entry = find_entry(app_label, segment)
    row = await get_row(entry, parse_pk(entry, pk))
    return render(
        request, "form.html", user, entry=entry, fields=edit_fields(entry, row),
        action=f"{entry.url}/{pk}", title=f"Edit {entry.model.__name__} {pk}",
        delete_url=f"{entry.url}/{pk}/delete",
    )


@router.post("/{app_label}/{segment}/{pk}")
async def edit(
    request: Request, app_label: str, segment: str, pk: str, user: Superuser
) -> Response:
    entry = find_entry(app_label, segment)
    pk_value = parse_pk(entry, pk)
    row = await get_row(entry, pk_value)
    form = await form_data(request)
    values, errors = forms.parse(entry.model, entry.model.Update, form, editing=True)
    ends_sessions = False
    if entry.is_user and not errors:
        if pk_value == user.id and (not values.get("is_active") or not values.get("is_superuser")):
            errors["__all__"] = "You cannot deactivate or remove superuser status from yourself."
        new_password = form.get("new_password", [""])[0]
        if new_password:
            from fyro.auth.passwords import hash_password

            values["password"] = hash_password(new_password)
        ends_sessions = bool(new_password) or (row.is_active and not values.get("is_active"))
    status = 422
    if not errors:
        try:
            if values:
                await entry.model.objects.filter(**{pk_name(entry): pk_value}).update(**values)
            if ends_sessions:
                from fyro.auth.sessions import end_user_sessions

                await end_user_sessions(pk_value)
            return redirect(entry.url)
        except IntegrityError as error:
            status, errors = conflict_errors(entry, error)
    return render(
        request, "form.html", user, status=status, entry=entry,
        fields=edit_fields(entry, row, errors, form), action=f"{entry.url}/{pk}",
        title=f"Edit {entry.model.__name__} {pk}", delete_url=f"{entry.url}/{pk}/delete",
        error=errors.get("__all__", ""),
    )


@router.get("/{app_label}/{segment}/{pk}/delete")
async def delete_page(
    request: Request, app_label: str, segment: str, pk: str, user: Superuser
) -> HTMLResponse:
    entry = find_entry(app_label, segment)
    row = await get_row(entry, parse_pk(entry, pk))
    return confirm(request, user, entry, [row])


@router.post("/{app_label}/{segment}/{pk}/delete")
async def delete(
    request: Request, app_label: str, segment: str, pk: str, user: Superuser
) -> Response:
    entry = find_entry(app_label, segment)
    pk_value = parse_pk(entry, pk)
    row = await get_row(entry, pk_value)
    if entry.is_user and pk_value == user.id:
        return confirm(request, user, entry, [row], status=422, error="You cannot delete yourself.")
    await entry.model.objects.filter(**{pk_name(entry): pk_value}).delete()
    return redirect(entry.url)
