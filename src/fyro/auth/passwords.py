import asyncio

from pwdlib import PasswordHash

# Argon2id with pwdlib's defaults (m=64 MiB, t=3, p=4), above OWASP's minimum.
_hasher = PasswordHash.recommended()
# Verified against for unknown usernames, so response time doesn't reveal which exist.
_DUMMY_HASH = _hasher.hash("fyro-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password off the event loop (Argon2 is deliberately slow)."""
    if password_hash is None:
        await asyncio.to_thread(_hasher.verify, password, _DUMMY_HASH)
        return False
    return await asyncio.to_thread(_hasher.verify, password, password_hash)
