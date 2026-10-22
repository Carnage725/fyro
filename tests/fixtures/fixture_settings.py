from fyro import Settings


class ProjectSettings(Settings):
    database_url: str = "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
    secret_key: str = "s" * 60
    installed_apps: list[str] = ["blog"]  # noqa: RUF012 (pydantic copies defaults)
