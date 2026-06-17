# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the qubes_policy resource and the shared policy core.

These run the real render/parse/validate logic (qrexec's parser is importable on
this dev box) against an in-memory FakePolicyClient, so file rendering, canonical
round-trip (no spurious diff), ordering and validation are all exercised."""

from unittest.mock import MagicMock

import pytest

from tf.utils import Diagnostics

from qubes_provider.utils import policy as policy_core
from qubes_provider.utils.policy import QubesPolicy
from qubes_provider.provider import QubesProvider
from qubes_provider.resources.policy import QubesPolicyResource
from tests.fakes import FakePolicyClient


def ctx():
    c = MagicMock()
    c.diagnostics.add_error = MagicMock()
    return c


def make_res(store=None):
    """A QubesPolicyResource backed by an in-memory store (returned for assertions)."""
    store = {} if store is None else store
    res = QubesPolicyResource(provider=MagicMock(),
                              backend=QubesPolicy(client=FakePolicyClient(store)))
    return res, store


RULES = [
    {"service": "securedrop.Proxy", "source": "sd-app", "target": "sd-proxy", "action": "allow"},
    {"service": "qubes.Gpg", "source": "@tag:sd-client", "target": "sd-gpg", "action": "allow"},
]


# --- provider wiring --------------------------------------------------------

def test_provider_wires_resource():
    assert QubesPolicyResource in QubesProvider().get_resources()


def test_resource_type_name_is_qubes_policy():
    p = QubesProvider()
    assert p.get_model_prefix() + QubesPolicyResource.get_name() == "qubes_policy"


def test_schema_builds():
    assert QubesPolicyResource.get_schema().to_pb() is not None


# --- create / read ----------------------------------------------------------

def test_create_writes_file_and_echoes_rules():
    r, store = make_res()
    state = r.create(ctx(), {"name": "31-securedrop-workstation", "rules": RULES})
    assert state["rules"] == RULES                      # echoed verbatim (no diff)
    assert state["id"] == "31-securedrop-workstation"
    content = store["31-securedrop-workstation"]
    assert "securedrop.Proxy\t*\tsd-app\tsd-proxy\tallow" in content
    assert "qubes.Gpg\t*\t@tag:sd-client\tsd-gpg\tallow" in content


def test_read_echoes_declared_without_diff():
    r, store = make_res()
    r.create(ctx(), {"name": "31-x", "rules": RULES})
    state = r.read(ctx(), {"name": "31-x", "rules": RULES})
    assert state["rules"] == RULES                      # canonical match -> echo, no drift


def test_read_missing_file_returns_none():
    r, _ = make_res()
    assert r.read(ctx(), {"name": "absent", "rules": RULES}) is None


def test_create_reports_invalid_rule():
    r, store = make_res()
    c = ctx()
    bad = [{"service": "qubes.Gpg", "source": "sd-app", "target": "sd-gpg", "action": "maybe"}]
    assert r.create(c, {"name": "31-bad", "rules": bad}) is None
    c.diagnostics.add_error.assert_called_once()
    assert "31-bad" not in store                        # nothing written


# --- params / ordering / round-trip -----------------------------------------

def test_all_params_render():
    r, store = make_res()
    rules = [
        {"service": "qubes.Gpg2", "source": "@tag:sd-client", "target": "sd-gpg",
         "action": "allow", "redirect": "sd-gpg"},
        {"service": "qubes.USBAttach", "source": "sys-usb",
         "target": "@tag:sd-export-target", "action": "allow", "user": "root"},
        {"service": "securedrop.Log", "source": "sd-log", "target": "sd-log",
         "action": "deny", "notify": "no"},
        {"service": "qubes.Filecopy", "source": "sd-log", "target": "@default",
         "action": "ask", "default_target": "sd-log"},
        {"service": "qubes.Gpg", "source": "@tag:sd-client", "target": "sd-gpg",
         "action": "allow", "argument": "+import"},
    ]
    r.create(ctx(), {"name": "31-p", "rules": rules})
    c = store["31-p"]
    assert "qubes.Gpg2\t*\t@tag:sd-client\tsd-gpg\tallow target=sd-gpg" in c
    assert "qubes.USBAttach\t*\tsys-usb\t@tag:sd-export-target\tallow user=root" in c
    assert "securedrop.Log\t*\tsd-log\tsd-log\tdeny notify=no" in c
    assert "qubes.Filecopy\t*\tsd-log\t@default\task default_target=sd-log" in c
    assert "qubes.Gpg\t+import\t@tag:sd-client\tsd-gpg\tallow" in c


def test_params_round_trip_via_import():
    r, store = make_res()
    rules = [
        {"service": "securedrop.Log", "source": "sd-log", "target": "sd-log",
         "action": "deny", "notify": "no"},
        {"service": "qubes.Gpg2", "source": "@tag:sd-client", "target": "sd-gpg",
         "action": "allow", "redirect": "sd-gpg"},
        {"service": "qubes.Gpg", "source": "@tag:sd-client", "target": "sd-gpg",
         "action": "allow", "argument": "+import"},
    ]
    r.create(ctx(), {"name": "31-rt", "rules": rules})
    state = r.import_(ctx(), "31-rt")                    # import adopts the file as content
    # the structured params survive a parse of that content
    assert policy_core.parse_to_maps(state["content"], include_source=True) == rules


def test_order_is_preserved():
    r, _ = make_res()
    r.create(ctx(), {"name": "31-o", "rules": RULES})
    state = r.import_(ctx(), "31-o")
    services = [x["service"]
               for x in policy_core.parse_to_maps(state["content"], include_source=True)]
    assert services == ["securedrop.Proxy", "qubes.Gpg"]


# --- update / delete / import ----------------------------------------------

def test_update_changes_rules():
    r, store = make_res()
    r.create(ctx(), {"name": "31-u", "rules": RULES})
    new = RULES[:1]
    state = r.update(ctx(), {"name": "31-u", "rules": RULES}, {"name": "31-u", "rules": new})
    assert state["rules"] == new
    assert "qubes.Gpg" not in store["31-u"]


def test_delete_removes_file():
    r, store = make_res()
    r.create(ctx(), {"name": "31-d", "rules": RULES})
    r.delete(ctx(), {"name": "31-d", "rules": RULES})
    assert "31-d" not in store


def test_import_missing_errors():
    r, _ = make_res()
    c = ctx()
    assert r.import_(c, "nope") is None
    c.diagnostics.add_error.assert_called_once()


# --- shared core normalization ---------------------------------------------

def test_parse_drops_wildcard_argument_and_defaults():
    # A hand-written file with explicit "*" argument and a default-on deny notify
    # normalizes to the minimal map form.
    content = (
        "qubes.Gpg\t*\tsd-app\tsd-gpg\tallow\n"
        "securedrop.Log\t*\tsd-log\tsd-log\tdeny\n"
    )
    maps = policy_core.parse_to_maps(content, include_source=True)
    assert maps == [
        {"service": "qubes.Gpg", "source": "sd-app", "target": "sd-gpg", "action": "allow"},
        {"service": "securedrop.Log", "source": "sd-log", "target": "sd-log", "action": "deny"},
    ]


def test_render_rule_requires_core_fields():
    with pytest.raises(Exception):
        policy_core.render_rule("sd-app", {"service": "qubes.Gpg", "action": "allow"})  # no target
    with pytest.raises(Exception):
        policy_core.render_rule("", {"service": "qubes.Gpg", "target": "x", "action": "allow"})


# --- content mode (raw / templated text) ------------------------------------

CONTENT = (
    "securedrop.Proxy * sd-app sd-proxy allow\n"
    "qubes.Gpg * sd-app sd-gpg allow\n"
)


def test_create_content_writes_verbatim_and_echoes():
    r, store = make_res()
    state = r.create(ctx(), {"name": "31-c", "content": CONTENT})
    assert state["content"] == CONTENT       # echoed verbatim
    assert state["rules"] is None
    assert store["31-c"] == CONTENT          # written as-is


def test_read_content_echoes_without_diff():
    r, _ = make_res()
    r.create(ctx(), {"name": "31-c", "content": CONTENT})
    state = r.read(ctx(), {"name": "31-c", "content": CONTENT, "rules": None})
    assert state["content"] == CONTENT       # canonical match -> echo, no drift


def test_read_content_ignores_cosmetic_drift():
    # A hand-added comment / blank line in the live file is not real drift: same rules.
    r, store = make_res()
    r.create(ctx(), {"name": "31-c", "content": CONTENT})
    store["31-c"] = "# edited by hand\n\n" + CONTENT
    state = r.read(ctx(), {"name": "31-c", "content": CONTENT, "rules": None})
    assert state["content"] == CONTENT       # still echoes declared (rules unchanged)


def test_read_content_surfaces_real_drift():
    r, store = make_res()
    r.create(ctx(), {"name": "31-c", "content": CONTENT})
    store["31-c"] = "qubes.Gpg * sd-app sd-gpg deny\n"   # rule changed out of band
    state = r.read(ctx(), {"name": "31-c", "content": CONTENT, "rules": None})
    assert state["content"] == "qubes.Gpg * sd-app sd-gpg deny\n"   # returns the file


def test_create_content_rejects_invalid():
    r, store = make_res()
    c = ctx()
    assert r.create(c, {"name": "31-bad", "content": "not a policy line\n"}) is None
    c.diagnostics.add_error.assert_called_once()
    assert "31-bad" not in store


def test_import_returns_content():
    r, _ = make_res()
    r.create(ctx(), {"name": "31-i", "content": CONTENT})
    state = r.import_(ctx(), "31-i")
    assert state["content"] == CONTENT
    assert state["rules"] is None


# --- validate: exactly one of content / rules -------------------------------

def test_validate_both_set_errors():
    r, _ = make_res()
    diags = Diagnostics()
    r.validate(diags, "qubes_policy", {"name": "x", "content": CONTENT, "rules": RULES})
    assert diags.has_errors()


def test_validate_neither_set_errors():
    r, _ = make_res()
    diags = Diagnostics()
    r.validate(diags, "qubes_policy", {"name": "x"})
    assert diags.has_errors()


def test_validate_exactly_one_ok():
    r, _ = make_res()
    for cfg in ({"name": "x", "content": CONTENT}, {"name": "x", "rules": RULES}):
        diags = Diagnostics()
        r.validate(diags, "qubes_policy", cfg)
        assert not diags.has_errors()
