# Releasing (maintainers)

Releases are built and published by GitHub Actions (`.github/workflows/release.yml`) using PyPI **Trusted Publishing**: no API token is stored anywhere.

## One-time setup

1. Create accounts on [pypi.org](https://pypi.org/account/register/) and [test.pypi.org](https://test.pypi.org/account/register/) (separate accounts), with two-factor authentication.
2. On each site, add a **pending trusted publisher** (*Your projects* → *Publishing* → *Add a new pending publisher*):
    - PyPI project name: `fyro`
    - Owner and repository: your GitHub user or org, and the repository name
    - Workflow name: `release.yml`
    - Environment: `pypi` on pypi.org, `testpypi` on test.pypi.org
3. In the GitHub repository, *Settings* → *Environments*: create `testpypi` and `pypi` (optionally require your approval for `pypi`).
4. *Settings* → *Pages*: set *Source* to **GitHub Actions** for the docs site.

## Each release

1. Update `version` in `pyproject.toml` and `CHANGELOG.md`; commit.
2. Check locally: `uv run ruff check && uv run pytest && uv build`.
3. Tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
4. The workflow checks the tag matches the version, builds, installs the wheel in a clean environment, publishes to TestPyPI, then to PyPI.
5. Check: `uv run --isolated --no-project --with fyro -- python -c "import fyro"`.
