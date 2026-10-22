"""The PLAN.md success test, scripted: empty folder to running API, through real processes."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.slow

BASE_URL = make_url(
    os.environ.get(
        "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
    )
)
DB_URL = BASE_URL.set(database="fyro_test_e2e")
PYTHON = sys.executable
FYRO = Path(sys.executable).parent / "fyro"  # the installed console script


def admin_sql(statement: str) -> None:
    async def go():
        engine = create_async_engine(BASE_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(go())


@pytest.fixture
def database():
    admin_sql("DROP DATABASE IF EXISTS fyro_test_e2e")
    admin_sql("CREATE DATABASE fyro_test_e2e")
    yield
    admin_sql("DROP DATABASE fyro_test_e2e")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_empty_folder_to_running_api(tmp_path, database):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FYRO_")}
    env["FYRO_DATABASE_URL"] = DB_URL.render_as_string(hide_password=False)
    timings: dict[str, float] = {}

    def run(step: str, *args: str, cwd: Path) -> str:
        start = time.monotonic()
        done = subprocess.run(
            args, check=False, cwd=cwd, env=env, capture_output=True, text=True, timeout=60
        )
        timings[step] = time.monotonic() - start
        assert done.returncode == 0, f"{step} failed:\n{done.stdout}\n{done.stderr}"
        return done.stdout + done.stderr

    run("fyro new", str(FYRO), "new", "mysite", cwd=tmp_path)
    root = tmp_path / "mysite"
    run("startapp", PYTHON, "manage.py", "startapp", "blog", cwd=root)
    (root / "blog" / "models.py").write_text(
        textwrap.dedent(
            """
            from fyro import Field, Model

            class Post(Model):
                title: str = Field(max_length=200)
                body: str
                secret: str = Field(write_only=True)
                published: bool = False
            """
        )
    )
    settings = root / "mysite" / "settings.py"
    settings.write_text(settings.read_text().replace("= []", '= ["blog"]'))
    made = run("makemigrations", PYTHON, "manage.py", "makemigrations", cwd=root)
    migrated = run("migrate", PYTHON, "manage.py", "migrate", cwd=root)
    assert "_initial.py" in made
    assert "Database is at" in migrated
    # Django-style: no nag about the generated dev key on every command
    assert "is insecure" not in made + migrated

    port = free_port()
    start = time.monotonic()
    server = subprocess.Popen(
        [PYTHON, "manage.py", "runserver", "--port", str(port)],  # default: reload on
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/docs").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            pytest.fail("runserver did not come up within 20 seconds")
        timings["runserver up"] = time.monotonic() - start

        created = httpx.post(
            f"{base}/api/blog/post", json={"title": "Hello", "body": "b", "secret": "s"}
        )
        assert created.status_code == 201
        assert created.json() == {"id": 1, "title": "Hello", "body": "b", "published": False}
        listed = httpx.get(f"{base}/api/blog/post").json()
        assert listed["total"] == 1 and listed["items"][0]["title"] == "Hello"
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        server_output, _ = server.communicate(timeout=10)  # also closes the pipe
    assert "Starting fyro" in server_output
    assert "is insecure" not in server_output  # also hidden in the reload child process

    print("\n" + "\n".join(f"{step:>16}: {seconds:.2f}s" for step, seconds in timings.items()))
    assert sum(timings.values()) < 60  # machine time; the human budget is 10 minutes
