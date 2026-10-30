import asyncio
import logging
import os
import re
import sys
import textwrap

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from fyro import Model, apps, migrations
from fyro.apps import Registry
from fyro.conf import ImproperlyConfigured, get_settings

BASE_URL = make_url(
    os.environ.get("FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro")
)
DB_URL = BASE_URL.set(database="fyro_test_migrations")
FILE_NAME = re.compile(r"^\d{4}_\d{2}_\d{2}_\d{4}-[0-9a-f]{12}_(?P<slug>\w+)\.py$")

POST = """
    from fyro import Field, Model

    class Post(Model):
        title: str = Field(max_length=200)
        slug: str = Field(max_length=50, unique=True)
        author: str = Field(index=True)
"""


def run_sql(url, *statements, autocommit=False):
    async def go():
        engine = create_async_engine(
            url, poolclass=NullPool, isolation_level="AUTOCOMMIT" if autocommit else None
        )
        try:
            async with engine.begin() as conn:
                result = None
                for statement in statements:
                    result = await conn.execute(text(statement))
                return result.all() if result is not None and result.returns_rows else None
        finally:
            await engine.dispose()

    return asyncio.run(go())


def columns(table):
    return {
        name: dtype
        for name, dtype in run_sql(
            DB_URL,
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = '{table}'",
        )
    }


def tables():
    rows = run_sql(DB_URL, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {name for (name,) in rows}


@pytest.fixture(scope="module", autouse=True)
def database():
    run_sql(BASE_URL, "DROP DATABASE IF EXISTS fyro_test_migrations", autocommit=True)
    run_sql(BASE_URL, "CREATE DATABASE fyro_test_migrations", autocommit=True)
    yield
    run_sql(BASE_URL, "DROP DATABASE fyro_test_migrations", autocommit=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway project: tmp/myproject/settings.py + tmp/blog/models.py."""
    run_sql(DB_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
    for name in list(os.environ):
        if name.startswith("FYRO_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("FYRO_SETTINGS", "myproject.settings")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    (tmp_path / "myproject").mkdir()
    (tmp_path / "myproject" / "__init__.py").touch()
    (tmp_path / "myproject" / "settings.py").write_text(
        "from fyro import Settings\n\n"
        "class ProjectSettings(Settings):\n"
        f"    database_url: str = {DB_URL.render_as_string(hide_password=False)!r}\n"
        f"    secret_key: str = {'k' * 60!r}\n"
        "    installed_apps: list[str] = ['blog']\n"
    )
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "__init__.py").touch()

    def write_models(source: str) -> None:
        """Replace blog/models.py and forget every loaded model, like a fresh process."""
        (tmp_path / "blog" / "models.py").write_text(textwrap.dedent(source))
        Model.registry.dispose()
        Model.metadata.clear()
        monkeypatch.setattr(apps, "registry", Registry())
        get_settings.cache_clear()
        for name in list(sys.modules):
            if name.split(".")[0] in ("blog", "myproject"):
                del sys.modules[name]

    write_models(POST)
    yield tmp_path, write_models
    Model.registry.dispose()
    Model.metadata.clear()
    get_settings.cache_clear()
    for name in list(sys.modules):
        if name.split(".")[0] in ("blog", "myproject"):
            del sys.modules[name]


def revision_files(root):
    return sorted((root / "migrations").glob("*.py"))


# --- makemigrations --------------------------------------------------------


def test_first_makemigrations_writes_initial_in_project_root(project):
    root, _ = project
    path = migrations.makemigrations()
    assert path.parent == root / "migrations"  # project root, not inside a package
    assert FILE_NAME.match(path.name).group("slug") == "initial"
    source = path.read_text()
    assert "op.create_table('blog_post'" in source
    assert revision_files(root) == [path]


def test_makemigrations_with_no_changes_writes_nothing(project):
    root, _ = project
    migrations.makemigrations()
    migrations.migrate()
    assert migrations.makemigrations() is None
    assert len(revision_files(root)) == 1


def test_added_field_gives_auto_revision_with_only_that_column(project):
    _, write_models = project
    migrations.makemigrations()
    migrations.migrate()
    write_models(POST + "        subtitle: str | None = None\n")
    path = migrations.makemigrations()
    assert FILE_NAME.match(path.name).group("slug") == "auto"
    source = path.read_text()
    assert "op.add_column('blog_post', sa.Column('subtitle'" in source
    assert "create_table" not in source


def test_custom_message_becomes_slug(project):
    path = migrations.makemigrations("add posts")
    assert FILE_NAME.match(path.name).group("slug") == "add_posts"


def test_makemigrations_when_database_is_behind_is_error(project):
    migrations.makemigrations()  # not applied yet
    with pytest.raises(ImproperlyConfigured, match=r"migrate\(\)"):
        migrations.makemigrations()


def test_rename_shows_as_drop_plus_add_and_warns(project, caplog):
    _, write_models = project
    migrations.makemigrations()
    migrations.migrate()
    # nullable: a NOT NULL field without a default is refused since phase 6.1
    write_models(POST.replace("author: str", "writer: str | None"))
    with caplog.at_level(logging.WARNING, logger="fyro.migrations"):
        path = migrations.makemigrations()
    source = path.read_text()
    assert "op.drop_column('blog_post', 'author')" in source
    assert "op.add_column('blog_post', sa.Column('writer'" in source
    assert re.search(r"blog_post.*author.*writer", caplog.text)


# --- migrate ---------------------------------------------------------------


def test_migrate_creates_tables_and_twice_is_noop(project):
    migrations.makemigrations()
    migrations.migrate()
    migrations.migrate()
    assert "blog_post" in tables()
    assert set(columns("blog_post")) == {"id", "title", "slug", "author"}


def test_migrated_schema_matches_models(project):
    migrations.makemigrations()
    migrations.migrate()
    assert migrations.makemigrations() is None  # autogenerate finds no difference


def test_constraint_names_follow_naming_convention(project):
    migrations.makemigrations()
    migrations.migrate()
    rows = run_sql(DB_URL, "SELECT indexname FROM pg_indexes WHERE tablename = 'blog_post'")
    assert {name for (name,) in rows} == {
        "pk_blog_post",
        "uq_blog_post_slug",
        "ix_blog_post_author",
    }


def test_migrate_down_to_revision_zero_and_back_up(project):
    _, write_models = project
    first = migrations.makemigrations()
    migrations.migrate()
    write_models(POST + "        subtitle: str | None = None\n")
    migrations.makemigrations()
    migrations.migrate()
    assert "subtitle" in columns("blog_post")

    first_rev = first.name.split("-")[1][:12]
    migrations.migrate(first_rev)
    assert "subtitle" not in columns("blog_post")

    migrations.migrate("zero")
    assert tables() == {"alembic_version"}

    migrations.migrate()
    assert "subtitle" in columns("blog_post")


def test_migrate_without_migrations_dir_is_error(project):
    with pytest.raises(ImproperlyConfigured, match="makemigrations"):
        migrations.migrate()


def test_migrate_unknown_target_is_error(project):
    migrations.makemigrations()
    with pytest.raises(ImproperlyConfigured, match="nope"):
        migrations.migrate("nope")


def test_two_heads_is_error_naming_both(project):
    root, _ = project
    first = migrations.makemigrations()
    first_rev = first.name.split("-")[1][:12]
    for rev in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
        (root / "migrations" / f"2026_01_01_0000-{rev}_branch.py").write_text(
            f"revision = {rev!r}\ndown_revision = {first_rev!r}\n"
            "branch_labels = None\ndepends_on = None\n\n"
            "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n"
        )
    with pytest.raises(ImproperlyConfigured, match=r"aaaaaaaaaaaa.*bbbbbbbbbbbb|bbbb.*aaaa"):
        migrations.migrate()
    with pytest.raises(ImproperlyConfigured, match="heads"):
        migrations.makemigrations()


async def test_calling_from_running_loop_is_error(project):
    with pytest.raises(RuntimeError, match="to_thread"):
        migrations.makemigrations()
    with pytest.raises(RuntimeError, match="to_thread"):
        migrations.migrate()


# --- enums -----------------------------------------------------------------

STATUS = """
    import enum
    from fyro import Model

    class Status(enum.Enum):
        DRAFT = "draft"
        LIVE = "live"
{extra}
    class Post(Model):
        status: Status = Status.DRAFT
"""


def test_enum_is_varchar_storing_value_and_new_member_needs_no_migration(project):
    _, write_models = project
    write_models(STATUS.format(extra=""))
    migrations.makemigrations()
    migrations.migrate()
    assert columns("blog_post")["status"] == "character varying"
    run_sql(DB_URL, "INSERT INTO blog_post (status) VALUES ('draft')")
    assert run_sql(DB_URL, "SELECT status FROM blog_post") == [("draft",)]
    assert not run_sql(DB_URL, "SELECT 1 FROM pg_type WHERE typname = 'status'")

    write_models(STATUS.format(extra='        ARCHIVED = "archived-for-good"\n'))
    assert migrations.makemigrations() is None


# --- adding NOT NULL fields to tables that already have rows ----------------


def add_field_to_table_with_rows(project, field: str, imports: str = ""):
    """Migrate POST, insert two rows, then add `field` and run makemigrations."""
    _, write_models = project
    migrations.makemigrations()
    migrations.migrate()
    run_sql(
        DB_URL,
        "INSERT INTO blog_post (title, slug, author) VALUES ('a', 'a', 'x'), ('b', 'b', 'x')",
    )
    source = POST.replace(
        "from fyro import Field, Model", imports + "from fyro import Field, Model"
    )
    write_models(source + f"        {field}\n")
    return migrations.makemigrations()


def column_state(name):
    (row,) = run_sql(
        DB_URL,
        "SELECT is_nullable, column_default FROM information_schema.columns "
        f"WHERE table_name = 'blog_post' AND column_name = '{name}'",
    )
    return row


@pytest.mark.parametrize(
    ("field", "column", "expected"),
    [
        ("views: int = 0", "views", 0),
        ("published: bool = True", "published", True),
        ("""label: str = "it's new" """, "label", "it's new"),
        ("ratio: float = 1.5", "ratio", 1.5),
    ],
    ids=["int", "bool", "str-with-quote", "float"],
)
def test_constant_default_fills_old_rows_and_leaves_no_db_default(project, field, column, expected):
    # bug: NotNullViolation on migrate (reproduced at the phase 7 gate)
    add_field_to_table_with_rows(project, field)
    migrations.migrate()
    assert run_sql(DB_URL, f"SELECT {column} FROM blog_post ORDER BY id") == [
        (expected,),
        (expected,),
    ]
    assert column_state(column) == ("NO", None)  # Python stays the only source of the default
    assert migrations.makemigrations() is None  # and no spurious diff afterwards


def test_enum_constant_default_fills_member_value(project):
    imports = "import enum\n\n    class Status(enum.Enum):\n        DRAFT = 'draft'\n        LIVE = 'live'\n\n    "
    add_field_to_table_with_rows(project, "status: Status = Status.LIVE", imports)
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT status FROM blog_post") == [("live",), ("live",)]
    assert column_state("status") == ("NO", None)


def test_uuid_factory_gives_each_old_row_its_own_value(project):
    path = add_field_to_table_with_rows(
        project, "token: uuid.UUID = Field(default_factory=uuid.uuid4)", "import uuid\n    "
    )
    assert "gen_random_uuid()" in path.read_text()
    migrations.migrate()
    tokens = run_sql(DB_URL, "SELECT token FROM blog_post")
    assert len({token for (token,) in tokens}) == 2 and all(tokens)
    assert column_state("token") == ("NO", None)
    assert migrations.makemigrations() is None


def test_now_factory_fills_old_rows(project):
    add_field_to_table_with_rows(
        project,
        "created: datetime = Field(default_factory=datetime.now)",
        "from datetime import datetime\n    ",
    )
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT count(*) FROM blog_post WHERE created IS NULL") == [(0,)]
    assert column_state("created") == ("NO", None)


def test_nullable_field_without_default_is_a_plain_add(project):
    path = add_field_to_table_with_rows(project, "note: str | None = None")
    assert "server_default" not in path.read_text()
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT note FROM blog_post") == [(None,), (None,)]


@pytest.mark.parametrize(
    ("field", "imports"),
    [
        ("views: int", ""),
        ("token: str = Field(default_factory=lambda: 'x')", ""),
    ],
    ids=["no-default", "unknown-factory"],
)
def test_not_null_field_without_usable_default_is_refused(project, field, imports):
    root, _ = project
    with pytest.raises(ImproperlyConfigured, match=r"Post\.(views|token).*(default|optional)"):
        add_field_to_table_with_rows(project, field, imports)
    assert len(revision_files(root)) == 1  # no broken migration written


def test_unique_field_with_constant_default_on_table_with_rows_is_refused(project):
    # bug: every existing row would get the same value, which the unique constraint rejects
    root, _ = project
    with pytest.raises(ImproperlyConfigured, match=r"Post\.code.*unique"):
        add_field_to_table_with_rows(
            project, 'code: str = Field(default="x", max_length=20, unique=True)'
        )
    assert len(revision_files(root)) == 1


def test_unique_uuid_field_on_table_with_rows_is_fine(project):
    add_field_to_table_with_rows(
        project,
        "token: uuid.UUID = Field(default_factory=uuid.uuid4, unique=True)",
        "import uuid\n    ",
    )
    migrations.migrate()
    tokens = run_sql(DB_URL, "SELECT token FROM blog_post")
    assert len({token for (token,) in tokens}) == 2


# --- making an optional field required --------------------------------------


def optional_then_required(project, before: str, after: str, imports: str = ""):
    """Migrate with an optional field, insert one NULL and one filled row, then require it."""
    _, write_models = project
    source = POST.replace(
        "from fyro import Field, Model", imports + "from fyro import Field, Model"
    )
    write_models(source + f"        {before}\n")
    migrations.makemigrations()
    migrations.migrate()
    run_sql(
        DB_URL,
        "INSERT INTO blog_post (title, slug, author) VALUES ('a', 'a', 'x'), ('b', 'b', 'x')",
    )
    write_models(source + f"        {after}\n")
    return migrations.makemigrations()


def test_optional_to_required_fills_nulls_from_constant_default(project):
    optional_then_required(project, "subtitle: str | None = None", 'subtitle: str = "untitled"')
    run_sql(DB_URL, "UPDATE blog_post SET subtitle = 'kept' WHERE slug = 'b'")  # one row non-NULL
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT subtitle FROM blog_post ORDER BY slug") == [
        ("untitled",),
        ("kept",),
    ]
    assert column_state("subtitle") == ("NO", None)
    assert migrations.makemigrations() is None


def test_optional_to_required_with_quote_in_default(project):
    optional_then_required(project, "subtitle: str | None = None", """subtitle: str = "it's" """)
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT DISTINCT subtitle FROM blog_post") == [("it's",)]


def test_optional_to_required_uuid_factory_gives_each_row_its_own_value(project):
    optional_then_required(
        project,
        "token: uuid.UUID | None = None",
        "token: uuid.UUID = Field(default_factory=uuid.uuid4)",
        "import uuid\n    ",
    )
    migrations.migrate()
    tokens = run_sql(DB_URL, "SELECT token FROM blog_post")
    assert len({token for (token,) in tokens}) == 2 and all(token for (token,) in tokens)


def test_optional_to_required_without_default_is_refused(project):
    root, _ = project
    with pytest.raises(ImproperlyConfigured, match=r"Post\.subtitle.*default"):
        optional_then_required(project, "subtitle: str | None = None", "subtitle: str")
    assert len(revision_files(root)) == 1


# --- relations (phase 13) --------------------------------------------------

AUTHOR = """
    from fyro import Field, ForeignKey, ManyToMany, Model

    class Author(Model):
        name: str = Field(max_length=100)

    class Post(Model):
        title: str = Field(max_length=200)
"""


def with_rows(project, post_field=""):
    """Migrate Author + Post (two posts, authors 1 and 2) and add `post_field` to Post."""
    _, write_models = project
    write_models(AUTHOR)
    migrations.makemigrations()
    migrations.migrate()
    run_sql(
        DB_URL,
        "INSERT INTO blog_author (name) VALUES ('ana'), ('bob')",
        "INSERT INTO blog_post (title) VALUES ('a'), ('b')",
    )
    write_models(AUTHOR + f"        {post_field}\n")


def on_delete_of(constraint):
    (row,) = run_sql(
        DB_URL, f"SELECT confdeltype::text FROM pg_constraint WHERE conname = '{constraint}'"
    )
    return row[0]


def test_foreign_key_migrates_up_and_down(project):
    _, write_models = project
    write_models(AUTHOR + '        author: Author = ForeignKey(on_delete="cascade")\n')
    source = migrations.makemigrations().read_text()
    assert source.index("create_table('blog_author'") < source.index("create_table('blog_post'")
    migrations.migrate()
    assert on_delete_of("fk_blog_post_author_id_blog_author") == "c"
    assert migrations.makemigrations() is None
    migrations.migrate("zero")
    assert tables() == {"alembic_version"}


def test_nullable_foreign_key_on_table_with_rows_is_a_plain_add(project):
    with_rows(project, 'author: Author | None = ForeignKey(on_delete="set_null", default=None)')
    migrations.makemigrations()
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT author_id FROM blog_post") == [(None,), (None,)]


def test_required_foreign_key_without_default_on_table_with_rows_is_refused(project):
    root, _ = project
    with_rows(project, 'author: Author = ForeignKey(on_delete="cascade")')
    with pytest.raises(
        ImproperlyConfigured,
        match=r"Post\.author_id: .*link to Author\. .*default=<id of an existing Author>.*Author \| None",
    ):
        migrations.makemigrations()
    assert len(revision_files(root)) == 1


def test_required_foreign_key_with_default_fills_old_rows(project):
    with_rows(project, 'author: Author = ForeignKey(on_delete="cascade", default=2)')
    migrations.makemigrations()
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT author_id FROM blog_post") == [(2,), (2,)]
    assert column_state("author_id") == ("NO", None)


def test_optional_to_required_foreign_key_without_default_is_refused(project):
    _, write_models = project
    with_rows(project, 'author: Author | None = ForeignKey(on_delete="set_null", default=None)')
    migrations.makemigrations()
    migrations.migrate()
    write_models(AUTHOR + '        author: Author = ForeignKey(on_delete="cascade")\n')
    with pytest.raises(ImproperlyConfigured, match=r"Post\.author_id: .*default=<id"):
        migrations.makemigrations()


def test_changing_on_delete_recreates_constraint(project):
    _, write_models = project
    with_rows(project, 'author: Author | None = ForeignKey(on_delete="cascade", default=None)')
    migrations.makemigrations()
    migrations.migrate()
    write_models(AUTHOR + '        author: Author | None = ForeignKey(on_delete="set_null")\n')
    assert migrations.makemigrations() is not None
    migrations.migrate()
    assert on_delete_of("fk_blog_post_author_id_blog_author") == "n"
    assert migrations.makemigrations() is None


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ('author: Author = ForeignKey(on_delete="cascade")', [("a", 1)]),
        ('author: Author | None = ForeignKey(on_delete="set_null")', [("a", 1), ("b", None)]),
    ],
    ids=["cascade-deletes-orphans", "set-null-clears-orphans"],
)
def test_plain_id_column_becomes_foreign_key_and_orphans_follow_on_delete(
    project, caplog, field, expected
):
    # fyro.auth's Session.user_id was a plain column before relations
    _, write_models = project
    plain = "author_id: int = Field(index=True)" if "cascade" in field else "author_id: int | None"
    write_models(AUTHOR + f"        {plain}\n")
    migrations.makemigrations()
    migrations.migrate()
    run_sql(
        DB_URL,
        "INSERT INTO blog_author (name) VALUES ('ana')",
        "INSERT INTO blog_post (title, author_id) VALUES ('a', 1), ('b', 99)",
    )
    write_models(AUTHOR + f"        {field}\n")
    with caplog.at_level(logging.WARNING, logger="fyro.migrations"):
        migrations.makemigrations()
    assert re.search(r"blog_post\.author_id.*missing blog_author", caplog.text)
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT title, author_id FROM blog_post ORDER BY title") == expected


# --- renames (phase 15) ----------------------------------------------------


def renamed(project, new_field, answer):
    """Migrate POST with two rows, replace `author: str = ...` and makemigrations, answering
    every rename question with `answer`. Returns (path, questions asked)."""
    _, write_models = project
    migrations.makemigrations()
    migrations.migrate()
    run_sql(
        DB_URL,
        "INSERT INTO blog_post (title, slug, author) VALUES ('a', 'a', 'x'), ('b', 'b', 'y')",
    )
    write_models(POST.replace("author: str = Field(index=True)", new_field))
    asked = []

    def ask(table, old, new):
        asked.append((table, old, new))
        return answer

    return migrations.makemigrations(ask_rename=ask), asked


def test_rename_answered_yes_keeps_data_and_downgrades(project):
    path, asked = renamed(project, "writer: str = Field(index=True)", True)
    assert asked == [("blog_post", "author", "writer")]
    source = path.read_text()
    assert "new_column_name='writer'" in source
    assert "drop_column" not in source
    migrations.migrate()
    assert run_sql(DB_URL, "SELECT writer FROM blog_post ORDER BY writer") == [("x",), ("y",)]
    assert migrations.makemigrations() is None
    (initial,) = [f for f in revision_files(project[0]) if f.name.endswith("_initial.py")]
    first = initial.name.split("-")[1].split("_")[0]
    migrations.migrate(first)
    assert run_sql(DB_URL, "SELECT author FROM blog_post ORDER BY author") == [("x",), ("y",)]


def test_rename_with_different_nullability_is_not_asked(project, caplog):
    with caplog.at_level(logging.WARNING, logger="fyro.migrations"):
        path, asked = renamed(project, "writer: str | None = None", True)
    # different nullability: not a rename candidate, never asked
    assert asked == []
    assert "op.drop_column('blog_post', 'author')" in path.read_text()
    assert re.search(r"blog_post.*author.*writer", caplog.text)


def test_rename_declined_keeps_drop_plus_add(project):
    root, _ = project
    # a required column with no default: dropping + adding it is refused, as before
    with pytest.raises(ImproperlyConfigured, match=r"Post\.writer"):
        renamed(project, "writer: str = Field(index=True)", False)
    assert len(revision_files(root)) == 1


def test_rename_of_different_type_is_not_asked(project):
    path, asked = renamed(project, "writer: int | None = None", True)
    assert asked == []
    assert "drop_column" in path.read_text()


# --- ManyToMany (phase 16) -------------------------------------------------


def test_many_to_many_adds_and_drops_its_join_table(project):
    _, write_models = project
    with_rows(project, "authors: list[Author] = ManyToMany()")
    source = migrations.makemigrations().read_text()
    assert "op.create_table('blog_post_authors'" in source
    assert "add_column" not in source  # blog_post itself is unchanged
    migrations.migrate()
    run_sql(DB_URL, "INSERT INTO blog_post_authors (post_id, author_id) VALUES (1, 2)")
    assert migrations.makemigrations() is None
    run_sql(DB_URL, "DELETE FROM blog_author WHERE id = 2")
    assert run_sql(DB_URL, "SELECT count(*) FROM blog_post_authors") == [(0,)]

    write_models(AUTHOR)
    assert "op.drop_table('blog_post_authors')" in migrations.makemigrations().read_text()
    migrations.migrate()
    assert "blog_post_authors" not in tables()
    migrations.migrate("zero")
    assert tables() == {"alembic_version"}
