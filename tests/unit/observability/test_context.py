import asyncio

import pytest

from app.observability.context import (
    bind_context,
    clear_context,
    context_fields,
    context_scope,
    current_context,
)


def test_nothing_bound_by_default():
    assert current_context() is None
    assert context_fields() == {}


def test_bind_creates_context_and_unset_fields_are_omitted():
    bind_context(request_id="req-1")
    assert context_fields() == {"request_id": "req-1"}


def test_unknown_field_raises_so_typos_cannot_widen_what_is_logged():
    with pytest.raises(TypeError, match="application_text"):
        bind_context(application_text="anything")


def test_values_are_single_line_bounded_and_blank_becomes_unset():
    bind_context(tenant_id="acme\nINFO forged line\r\x00", screening_id="x" * 500, task_id="   ")
    fields = context_fields()
    assert "\n" not in fields["tenant_id"] and "\r" not in fields["tenant_id"]
    assert len(fields["screening_id"]) == 128
    assert "task_id" not in fields


def test_scope_inherits_overrides_and_restores():
    bind_context(request_id="outer", tenant_id="t1")
    with context_scope(tenant_id="t2", task_id="job") as inner:
        assert inner.as_dict() == {"request_id": "outer", "tenant_id": "t2", "task_id": "job"}
        bind_context(screening_id="s1")
    assert context_fields() == {"request_id": "outer", "tenant_id": "t1"}


def test_scope_without_parent_cleans_up():
    with context_scope(request_id="r"):
        assert context_fields() == {"request_id": "r"}
    assert current_context() is None


async def test_binding_inside_a_child_task_is_visible_to_the_scope_owner():
    """The reason the context is a shared mutable object: middleware must see the tenant that a
    dependency bound deep inside the request."""

    with context_scope(request_id="req-1"):

        async def handler():
            bind_context(tenant_id="acme")

        await asyncio.create_task(handler())
        await asyncio.to_thread(bind_context, actor_hash="abc123")
        assert context_fields() == {
            "request_id": "req-1",
            "tenant_id": "acme",
            "actor_hash": "abc123",
        }


async def test_sibling_scopes_do_not_leak_into_each_other():
    async def one(name: str, seen: dict):
        with context_scope(request_id=name):
            await asyncio.sleep(0)
            seen[name] = context_fields()["request_id"]

    seen: dict[str, str] = {}
    await asyncio.gather(one("a", seen), one("b", seen))
    assert seen == {"a": "a", "b": "b"}
    clear_context()
