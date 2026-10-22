from datetime import UTC, datetime

from fyro import Field, Model


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Model):
    username: str = Field(max_length=150, unique=True)
    email: str | None = None
    # The Argon2 hash. Excluded from every schema, so it never reaches a response.
    password: str = Field(write_only=True)
    is_active: bool = True
    is_superuser: bool = False
    date_joined: datetime = Field(default_factory=utcnow, read_only=True)

    class Meta:
        api = False
        create_exclude = ("password",)
        update_exclude = ("password",)


class Session(Model):
    # SHA-256 of the token: a leaked table holds no usable sessions.
    token_hash: str = Field(max_length=64, unique=True)
    user_id: int = Field(index=True)  # a plain column until relations (v0.2)
    created_at: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    expires_at: datetime

    class Meta:
        api = False
