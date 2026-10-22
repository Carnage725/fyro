# The Idea

## What we are building

We are building a web framework on top of FastAPI that feels like Django. FastAPI stays under the hood and does the web work. Our framework adds everything Django gives you: a clear way to structure a project, a model system, migrations, auth, and an admin panel. In short, FastAPI is the engine and we are building the car around it.

The one line pitch: Django's structure with FastAPI's typing and async speed.

## Why we are building it

FastAPI is great, but it gives you wood and no instructions. Every new project starts with the same two days of setup: database sessions, migrations, settings, auth, folder layout, and then an admin you have to build yourself. Every team solves these problems differently, so no two FastAPI projects look alike.

Django is the opposite. It gives you flat-pack furniture with numbered steps. A new developer joins and knows where everything lives within five minutes. But Django was born in a sync world, its type hints are an afterthought, and you write the same fields three times (model, serializer, view).

Nobody has built the middle. A framework with Django's opinions and FastAPI's modern core. That gap is the reason this project exists.

## The real product is the answer to "where does this go?"

Django did not win because of its features. It won because there is one obvious way to do things. Our framework must have strong opinions. Opinions must be enforced by code, not by documentation. If the structure only lives in the docs, people drift away in a week. If the generator creates it and auto-discovery requires it, people follow it.

## The core idea: one model does everything

You write a class once, with its fields. From that single class the framework builds the database table, the validation schemas for create, read and update, the CRUD API endpoints, and the admin screen.

In plain FastAPI you write a database model, a create schema, a read schema and an update schema by hand and hope they stay in sync. Here you write one blueprint and everything is drawn from it.

Fields carry small flags like read_only and write_only. A password is write-only, an id is read-only. The flags decide which fields appear in which generated schema. Everything is automatic by default, and the user can override any piece. Auto by default, escape hatch always. This is the part Django REST Framework got wrong and the part SQLModel never finished.

We will learn from SQLModel's code but not build on top of it. Our Model class is our product, so we write our own thin layer. We borrow the plumbing (Pydantic for validation, SQLAlchemy for the database) and own the generation logic and the flags. That is our moat.

## How it works under the hood

It is all Python. The magic happens once, at startup: a metaclass reads each model class, collects its fields, and generates the table and schemas. A loader reads the list of installed apps, imports each one's models and routes, and wires them into FastAPI. After that, every request just runs a fairly dumb loop. So the cleverness lives in Python at startup, and the speed lives in the per-request path.

## Where Rust comes in, and where it does not

Rust is for speed, but only where speed is actually lost. Pydantic v2 is already Rust, and orjson is already Rust. Route handlers mostly wait on the database, and Rust cannot speed up waiting.

The real win is the data path. Normally database rows become Python objects, then Pydantic objects, then dicts, then JSON. Python builds thousands of objects just to throw them away. With a Rust engine, rows go from the database straight to JSON bytes and Python never touches them. That is the same idea as Polars: Python describes the work, Rust does it.

We build this last, after real users show us where it hurts. The trick is to design a clean engine seam early. The engine takes a query, runs it and returns rows. We write it in Python first, then swap in a Rust version later through PyO3 and maturin. The user's API never changes.

## What we are aiming for, in build order

1. Typed settings. One settings class for the database URL, secret key and installed apps. Everything reads from it.
2. App loader. Reads the installed apps, auto-discovers each app's models and routes, and collects them. This is the skeleton.
3. The Model class. One class produces the table and the create, read and update schemas. This is the heart. If it does not feel magical, stop and fix it before moving on.
4. Hidden database engine and session. Async SQLAlchemy under the hood. The user never writes a get_db function. Postgres only at first.
5. Auto CRUD routes. A model in, a full API out, with filtering and pagination, mounted on FastAPI automatically. After this step we have a demo and we show people.
6. Migrations. We wrap Alembic behind makemigrations and migrate. We do not write our own diff engine.
7. The CLI. Commands like new, startapp, runserver, makemigrations, migrate and createsuperuser. By now each command just calls things we already built.
8. Auth and permissions. A built-in user model, login with sessions and JWT, and simple per-model permissions.
9. The admin. HTMX screens that call our own auto-generated API: list, search, edit, delete and a relation picker. After this step we have a product.
10. The Rust engine swap. Profile real usage, then replace the slowest path with Rust.

The three that carry everything are the app loader, the Model class and the auto CRUD. Get those right and the rest is plumbing.

## What we are deliberately not building yet

Real-time WebSocket admin, OAuth providers, rate limiting and CSRF extras, background tasks, signals, and auto-mocked testing. These are good ideas but not essential. A plain test client is enough for now. We add the rest after launch when users ask for it.

## Early decisions

Postgres only to start. This cuts the database work in half. Relations between models, like author pointing to a user, wait until version 0.2, because relations are the hardest part of any ORM layer and flat tables are enough to prove the idea. The admin is built with HTMX and ships inside the package, so users never touch Node. The admin is a client of our own generated API, which means it comes almost free and tests our API every day.

## How we know we are winning

The test is simple. A developer writes one model class, runs one command, and gets a working database table, a working API with docs, and a working admin page, with no glue code and no decisions about where files go. If that takes under ten minutes and feels like magic, the idea is alive. If it does not, we fix the Model class before building anything else.

## The rules we follow

The API is the product. Rust is an implementation detail we swap in later, and the API must never change when we do.
Wrap, do not rewrite. Use Alembic, SQLAlchemy and Pydantic instead of building our own versions.
Strong opinions beat more options.
Show a working demo as early as possible and let real users tell us what to build next.