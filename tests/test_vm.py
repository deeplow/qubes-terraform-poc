"""Unit tests for the qubes_vm resource and data source (no real Qubes needed).

Mirrors qubes-ansible semantics: "*default*" -> qubesadmin.DEFAULT (Reset) and
round-trips via property_is_default().
"""

from unittest.mock import MagicMock

import pytest
import qubesadmin
from qubesadmin import exc as qexc

from qubes_provider import client
from qubes_provider.client import QubesProviderError, enforce_properties
from qubes_provider.data_sources.vm import QubesVMDataSource
from qubes_provider.provider import QubesProvider
from qubes_provider.resources.vm import QubesVMResource
from tests.fakes import FakeApp


@pytest.fixture
def app(monkeypatch):
    fake = FakeApp()
    monkeypatch.setattr(client, "get_app", lambda: fake)
    monkeypatch.setattr("qubes_provider.resources.vm.get_app", lambda: fake)
    monkeypatch.setattr("qubes_provider.data_sources.vm.get_app", lambda: fake)
    return fake


def ctx():
    c = MagicMock()
    c.diagnostics.add_error = MagicMock()
    return c


def res():
    return QubesVMResource(provider=MagicMock())


# --- provider wiring --------------------------------------------------------

def test_resource_type_name_is_qubes_vm():
    p = QubesProvider()
    assert p.get_model_prefix() + QubesVMResource.get_name() == "qubes_vm"
    assert QubesVMResource in p.get_resources()
    assert QubesVMDataSource in p.get_data_sources()


def test_schemas_build():
    assert QubesVMResource.get_schema().to_pb() is not None
    assert QubesVMDataSource.get_schema().to_pb() is not None


# --- create -----------------------------------------------------------------

def test_create_explicit_template(app):
    c = ctx()
    state = res().create(c, {"name": "tf-work", "vm_class": "AppVM", "label": "blue",
                             "template": "fedora-40", "memory": 2048})
    c.diagnostics.add_error.assert_not_called()
    assert "tf-work" in app.domains
    assert state["template"] == "fedora-40"
    assert state["memory"] == 2048
    assert state["provisioned"] is True
    # template was set by add_new_vm, not re-written by enforce.
    assert "template" not in app.domains["tf-work"].writes


def test_create_default_template_roundtrips_without_reset(app):
    # "*default*" -> created with the default template value (qubesd forbids
    # unsetting template) -> no write needed -> reads back "*default*".
    state = res().create(ctx(), {"name": "tf-d", "vm_class": "AppVM", "label": "red",
                                 "template": "*default*"})
    assert state["template"] == "*default*"
    assert "template" not in app.domains["tf-d"].writes  # no Reset/Set issued


def test_create_default_netvm_is_noop_and_roundtrips(app):
    # netvm omitted is computed; "*default*" already default -> no write.
    state = res().create(ctx(), {"name": "tf-n", "vm_class": "AppVM", "label": "red",
                                 "netvm": "*default*"})
    assert state["netvm"] == "*default*"
    assert "netvm" not in app.domains["tf-n"].writes


def test_create_explicit_no_netvm(app):
    state = res().create(ctx(), {"name": "tf-x", "vm_class": "AppVM", "label": "red",
                                 "netvm": ""})
    assert state["netvm"] == ""  # explicit none, distinct from "*default*"
    assert app.domains["tf-x"].property_is_default("netvm") is False


def test_create_rolls_back_on_enforce_failure(app, monkeypatch):
    # If enforce fails after add_new_vm, the partially-created qube is removed.
    monkeypatch.setattr("qubes_provider.resources.vm.enforce_properties",
                        MagicMock(side_effect=RuntimeError("boom")))
    c = ctx()
    assert res().create(c, {"name": "tf-rb", "vm_class": "AppVM", "label": "red"}) is None
    assert "tf-rb" not in app.domains          # rolled back
    c.diagnostics.add_error.assert_called_once()


def test_create_reports_error(app, monkeypatch):
    monkeypatch.setattr(app, "add_new_vm",
                        MagicMock(side_effect=RuntimeError("name already used")))
    c = ctx()
    assert res().create(c, {"name": "dup", "vm_class": "AppVM", "label": "red"}) is None
    c.diagnostics.add_error.assert_called_once()


# --- read -------------------------------------------------------------------

def test_read_missing_returns_none(app):
    assert res().read(ctx(), {"name": "ghost"}) is None


def test_read_existing(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    state = res().read(ctx(), {"name": "tf-work"})
    assert state["name"] == "tf-work"
    assert state["template"] == "fedora-40"


def test_read_reports_daemon_access_error(app, monkeypatch):
    class Boom:
        def __contains__(self, k):
            raise RuntimeError("Request refused")

    monkeypatch.setattr(app, "domains", Boom())
    c = ctx()
    assert res().read(c, {"name": "tf-work"}) is None
    c.diagnostics.add_error.assert_called_once()


# --- update -----------------------------------------------------------------

def test_update_changes_label_and_memory(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    state = res().update(ctx(),
                         {"name": "tf-work", "label": "blue", "memory": 400},
                         {"name": "tf-work", "label": "red", "memory": 1024})
    assert state["label"] == "red"
    assert state["memory"] == 1024


def test_update_to_default(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.netvm = "sys-firewall"
    vm.writes.clear()
    state = res().update(ctx(),
                         {"name": "tf-work", "netvm": "sys-firewall"},
                         {"name": "tf-work", "netvm": "*default*"})
    # set to the current default value (not a Reset) -> round-trips to "*default*".
    assert state["netvm"] == "*default*"
    assert "netvm" in app.domains["tf-work"].writes


def test_update_default_to_default_is_idempotent(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.writes.clear()
    res().update(ctx(),
                 {"name": "tf-work", "netvm": "*default*"},
                 {"name": "tf-work", "netvm": "*default*"})
    assert "netvm" not in vm.writes  # already default-following -> no write


def test_update_explicit_none_is_idempotent(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.netvm = ""          # explicit none
    vm.writes.clear()
    res().update(ctx(),
                 {"name": "tf-work", "netvm": ""},
                 {"name": "tf-work", "netvm": ""})
    assert "netvm" not in vm.writes  # None normalized to "" -> unchanged


# --- delete / import --------------------------------------------------------

def test_delete_removes_vm(app):
    app.add_new_vm("AppVM", "tf-work", "blue")
    res().delete(ctx(), {"name": "tf-work"})
    assert "tf-work" not in app.domains


def test_delete_kills_running_vm(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue")
    object.__setattr__(vm, "_running", True)
    res().delete(ctx(), {"name": "tf-work"})
    assert "tf-work" not in app.domains


def test_import_existing(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    assert res().import_(ctx(), "tf-work")["name"] == "tf-work"


def test_import_missing(app):
    c = ctx()
    assert res().import_(c, "nope") is None
    c.diagnostics.add_error.assert_called_once()


# --- template_for_dispvms / DispVM ------------------------------------------

def test_set_template_for_dispvms(app):
    state = res().create(ctx(), {"name": "tf-dt", "vm_class": "AppVM", "label": "red",
                                 "template_for_dispvms": True})
    assert state["template_for_dispvms"] is True
    assert app.domains["tf-dt"].template_for_dispvms is True


def test_create_dispvm_on_appvm_template(app):
    app.add_new_vm("AppVM", "tf-dt", "red")
    state = res().create(ctx(), {"name": "tf-disp", "vm_class": "DispVM",
                                 "label": "green", "template": "tf-dt"})
    assert state["vm_class"] == "DispVM"
    assert state["template"] == "tf-dt"


# --- enforce_properties typed error mapping ---------------------------------

def test_enforce_maps_typed_errors():
    class BoomVM:
        klass = "AppVM"

        def property_is_default(self, name):
            return False

        @property
        def memory(self):
            return 400

        @memory.setter
        def memory(self, value):
            raise qexc.QubesValueError("bad")

    with pytest.raises(QubesProviderError, match="invalid value"):
        enforce_properties(None, BoomVM(), {"memory": 99999})
