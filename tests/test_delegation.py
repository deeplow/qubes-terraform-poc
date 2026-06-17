# SPDX-License-Identifier: GPL-3.0-or-later
"""Delegation smoke tests: drive the real qubes-ansible QubeModule / qube_facts
against the in-memory fakes, through the adapter's drivers. Proves the harness +
wiring (the bundled collection actually runs and reads back)."""

from qubes_provider.utils.qubes_adapter import QubesVmAdapter


def _adapter():
    return QubesVmAdapter()


def test_create_enforce_and_read_back(qube_env):
    a = _adapter()
    planned = {
        "name": "tf-x", "klass": "AppVM", "label": "red",
        "template": "fedora-40",
        "properties": {"netvm": "", "memory": "512"},
        "features": {"service.foo": "1"},
        "tags": ["team-x"],
    }
    result = a._run_module(a.desired_to_qube_params(planned))

    assert result["created"] is True
    assert result["changed"] is True
    assert "tf-x" in qube_env.domains

    facts = a._fetch_facts("tf-x")
    assert facts["name"] == "tf-x"
    assert facts["state"] == "halted"
    assert facts["properties"]["template"] == "fedora-40"
    assert facts["properties"]["memory"] == "512"
    assert facts["features"]["service.foo"] == "1"
    assert "team-x" in facts["tags"]


def test_read_absent_returns_none(qube_env):
    assert _adapter()._fetch_facts("ghost") is None


def test_default_template_create_is_noop_then_present(qube_env):
    # No template given -> qubesd default; state present; reads back as a real VM.
    a = _adapter()
    a._run_module(a.desired_to_qube_params({"name": "tf-d", "klass": "AppVM", "label": "blue"}))
    facts = a._fetch_facts("tf-d")
    assert facts["name"] == "tf-d"
    assert facts["default_properties"]["netvm"] is True  # untouched -> default-following


def test_delete_via_absent_state(qube_env):
    a = _adapter()
    a._run_module(a.desired_to_qube_params({"name": "tf-rm", "klass": "AppVM", "label": "red"}))
    assert "tf-rm" in qube_env.domains
    a.delete("tf-rm")
    assert "tf-rm" not in qube_env.domains
