"""Property test for schema generation (no database). Kept apart from test_properties.py:
each example clears the global model registry, which would wipe that module's models."""

import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fyro import Field, Model, apps
from fyro.apps import Registry

FIXTURES = Path(__file__).parent / "fixtures"


# --- invariant 4: write_only fields never appear in Read ----------------------


@settings(max_examples=40, deadline=None)
@given(
    flags=st.lists(
        st.sampled_from(["plain", "read_only", "write_only"]), min_size=1, max_size=6
    )
)
def test_read_schema_never_contains_write_only(flags):
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(FIXTURES))
        mp.setattr(apps, "registry", Registry())
        apps.registry.populate(["blog"])
        try:
            namespace = {"__module__": "blog.models", "__annotations__": {}}
            for i, flag in enumerate(flags):
                namespace["__annotations__"][f"f{i}"] = str
                kwargs = {flag: True} if flag != "plain" else {}
                namespace[f"f{i}"] = Field(default="x", **kwargs)
            model = type("Shape", (Model,), namespace)
            write_only = {f"f{i}" for i, flag in enumerate(flags) if flag == "write_only"}
            read_only = {f"f{i}" for i, flag in enumerate(flags) if flag == "read_only"}
            assert not write_only & set(model.Read.model_fields)
            assert not read_only & set(model.Create.model_fields)
            assert not read_only & set(model.Update.model_fields)
        finally:
            Model.registry.dispose()
            Model.metadata.clear()
            for name in list(sys.modules):
                if name.split(".")[0] == "blog":
                    del sys.modules[name]
