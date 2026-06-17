# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the qubes_prefs resource (dom0 global preferences via the Admin API).

The real QubesPrefs read/enforce/reset logic runs against the in-memory FakeApp by
pointing the module's qubesadmin at it."""

from unittest.mock import MagicMock

import pytest
import qubesadmin

from qubes_provider.provider import QubesProvider
from qubes_provider.resources import prefs
from qubes_provider.resources.prefs import QubesPrefsResource


def ctx():
    c = MagicMock()
    c.diagnostics.add_error = MagicMock()
    return c


def res():
    return QubesPrefsResource(provider=MagicMock())


@pytest.fixture
def prefs_env(monkeypatch, fake_app):
    class _QubesShim:
        DEFAULT = qubesadmin.DEFAULT          # the Reset sentinel the resource uses
        @staticmethod
        def Qubes():
            return fake_app

    monkeypatch.setattr(prefs, "qubesadmin", _QubesShim)
    return fake_app


# --- create / read ----------------------------------------------------------

def test_create_sets_declared_only(prefs_env):
    state = res().create(ctx(), {"default_netvm": "fedora-40"})   # differs from initial
    assert prefs_env._global_props["default_netvm"] == "fedora-40"
    assert state["default_netvm"] == "fedora-40"
    assert state["clockvm"] is None          # undeclared -> null (non-computed)
    assert state["id"] == "dom0"


def test_read_projects_declared_subset(prefs_env):
    res().create(ctx(), {"default_netvm": "fedora-40"})
    state = res().read(ctx(), {"default_netvm": "fedora-40"})
    assert state["default_netvm"] == "fedora-40"
    assert state["default_kernel"] is None   # not declared -> not reported


# --- update -----------------------------------------------------------------

def test_update_changes_value(prefs_env):
    res().create(ctx(), {"default_netvm": "fedora-40"})
    state = res().update(ctx(), {"default_netvm": "fedora-40"},
                         {"default_netvm": "sys-firewall"})
    assert prefs_env._global_props["default_netvm"] == "sys-firewall"
    assert state["default_netvm"] == "sys-firewall"


def test_removing_a_property_resets_it_to_default(prefs_env):
    res().create(ctx(), {"default_netvm": "fedora-40"})
    assert not prefs_env.property_is_default("default_netvm")
    # drop default_netvm from config entirely (no *default* sentinel)
    state = res().update(ctx(), {"default_netvm": "fedora-40"}, {})
    assert prefs_env.property_is_default("default_netvm")          # reset to default
    assert prefs_env._global_props["default_netvm"] == "default-netvm"
    assert state["default_netvm"] is None


# --- delete -----------------------------------------------------------------

def test_delete_resets_managed_only(prefs_env):
    res().create(ctx(), {"default_netvm": "fedora-40"})
    prefs_env._global_props["default_dispvm"] = "sd-viewer"   # unmanaged, set out of band
    res().delete(ctx(), {"default_netvm": "fedora-40"})
    assert prefs_env.property_is_default("default_netvm")     # managed -> reset
    assert prefs_env._global_props["default_dispvm"] == "sd-viewer"  # unmanaged -> untouched


def test_reset_propagates_failures(monkeypatch):
    # A failed reset (e.g. the mgmt qube lacks admin.property.Reset policy on dom0)
    # must NOT be swallowed: it has to surface so the destroy fails on qubes_prefs,
    # not silently leave the global set and later fail on a qube that references it.
    class _DeniedApp:
        def property_is_default(self, prop):
            return False
        def __setattr__(self, key, value):
            raise RuntimeError("Request refused")

    class _Shim:
        DEFAULT = qubesadmin.DEFAULT
        @staticmethod
        def Qubes():
            return _DeniedApp()

    monkeypatch.setattr(prefs, "qubesadmin", _Shim)
    with pytest.raises(RuntimeError, match="Request refused"):
        prefs.QubesPrefs().reset(["default_dispvm"])


# --- schema / wiring --------------------------------------------------------

def test_schema_is_dynamic(prefs_env):
    attrs = {a.name for a in QubesPrefsResource.get_schema().attributes}
    assert attrs == set(prefs_env.property_list()) | {"id"}
    assert QubesPrefsResource.get_schema().to_pb() is not None


def test_provider_wires_resource():
    assert QubesPrefsResource in QubesProvider().get_resources()
