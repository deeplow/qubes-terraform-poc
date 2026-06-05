"""Unit tests for the qubes_vm resource and data source (no real Qubes needed).

The provider passes generic property/feature/tag bags through qubesadmin; "*default*"
means the property's current Qubes default and round-trips via property_is_default /
GetDefault.
"""

from unittest.mock import MagicMock

import pytest
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


def make(name="tf-work", vm_class="AppVM", label="blue", template=None,
         properties=None, features=None, tags=None,
         shutdown_if_required=None, force_shutdown=None):
    return {"name": name, "vm_class": vm_class, "label": label, "template": template,
            "properties": properties, "features": features, "tags": tags,
            "shutdown_if_required": shutdown_if_required, "force_shutdown": force_shutdown}


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

def test_create_with_properties(app):
    c = ctx()
    state = res().create(c, make(template="fedora-40",
                                 properties={"netvm": "", "memory": "2048"}))
    c.diagnostics.add_error.assert_not_called()
    assert "tf-work" in app.domains
    assert state["label"] == "blue"
    assert state["template"] == "fedora-40"
    assert state["properties"]["netvm"] == ""      # explicit none round-trips
    assert state["properties"]["memory"] == "2048"


def test_create_default_template_roundtrips_no_write(app):
    state = res().create(ctx(), make(name="tf-d", template="*default*"))
    assert state["template"] == "*default*"
    assert "template" not in app.domains["tf-d"].writes  # already at default


def test_create_default_property_is_noop(app):
    # netvm="*default*" on a fresh VM (already default) -> no write.
    state = res().create(ctx(), make(name="tf-n", properties={"netvm": "*default*"}))
    assert state["properties"]["netvm"] == "*default*"
    assert "netvm" not in app.domains["tf-n"].writes


def test_create_features_and_tags(app):
    state = res().create(ctx(), make(name="tf-f",
                                     features={"service.foo": "1"},
                                     tags={"team-x"}))
    assert state["features"] == {"service.foo": "1"}
    assert state["tags"] == ["team-x"]
    assert app.domains["tf-f"].features["service.foo"] == "1"
    assert "team-x" in app.domains["tf-f"].tags


def test_create_reports_error(app, monkeypatch):
    monkeypatch.setattr(app, "add_new_vm",
                        MagicMock(side_effect=RuntimeError("name already used")))
    c = ctx()
    assert res().create(c, make(name="dup")) is None
    c.diagnostics.add_error.assert_called_once()


def test_create_rolls_back_on_enforce_failure(app, monkeypatch):
    monkeypatch.setattr("qubes_provider.resources.vm.enforce_properties",
                        MagicMock(side_effect=RuntimeError("boom")))
    c = ctx()
    assert res().create(c, make(name="tf-rb")) is None
    assert "tf-rb" not in app.domains
    c.diagnostics.add_error.assert_called_once()


# --- read -------------------------------------------------------------------

def test_read_missing_returns_none(app):
    assert res().read(ctx(), make(name="ghost")) is None


def test_read_reports_daemon_access_error(app, monkeypatch):
    class Boom:
        def __contains__(self, k):
            raise RuntimeError("Request refused")

    monkeypatch.setattr(app, "domains", Boom())
    c = ctx()
    assert res().read(c, make()) is None
    c.diagnostics.add_error.assert_called_once()


# --- update -----------------------------------------------------------------

def test_update_changes_property(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    state = res().update(ctx(),
                         make(properties={"memory": "400"}),
                         make(label="red", properties={"memory": "1024"}))
    assert state["label"] == "red"
    assert state["properties"]["memory"] == "1024"


def test_update_property_to_default(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.netvm = "sys-firewall"
    vm.writes.clear()
    state = res().update(ctx(),
                         make(properties={"netvm": "sys-firewall"}),
                         make(properties={"netvm": "*default*"}))
    assert state["properties"]["netvm"] == "*default*"
    assert "netvm" in vm.writes  # set to the default value


def test_update_default_to_default_is_idempotent(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.writes.clear()
    res().update(ctx(),
                 make(properties={"netvm": "*default*"}),
                 make(properties={"netvm": "*default*"}))
    assert "netvm" not in vm.writes


def test_update_removes_dropped_feature_and_tag(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.features["service.foo"] = "1"
    vm.tags.add("team-x")
    res().update(ctx(),
                 make(features={"service.foo": "1"}, tags={"team-x"}),
                 make(features={}, tags=set()))
    assert "service.foo" not in vm.features
    assert "team-x" not in vm.tags


def test_tags_managed_subset_ignores_auto_tags(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.tags.add("created-by-work")           # auto-tag, not declared
    state = res().update(ctx(),
                         make(tags=set()),
                         make(tags={"team-x"}))
    assert state["tags"] == ["team-x"]       # auto-tag never reported
    assert "created-by-work" in vm.tags      # and never removed


# --- shutdown-for-template-update (mirrors Ansible) -------------------------

def test_template_change_running_without_flag_errors(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    object.__setattr__(vm, "_running", True)
    c = ctx()
    out = res().update(c, make(template="fedora-40"),
                       make(template="default-template"))
    assert out is None
    c.diagnostics.add_error.assert_called_once()
    assert "running" in str(c.diagnostics.add_error.call_args).lower()
    assert vm.is_running()  # not shut down


def test_template_change_running_with_flag_shuts_down(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    object.__setattr__(vm, "_running", True)
    state = res().update(ctx(), make(template="fedora-40"),
                         make(template="default-template", shutdown_if_required=True))
    assert vm.is_halted()                       # shut down first
    assert state["template"] == "default-template"
    assert state["shutdown_if_required"] is True  # flag echoed


def test_template_change_halted_succeeds(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    state = res().update(ctx(), make(template="fedora-40"),
                         make(template="default-template"))
    assert state["template"] == "default-template"


def test_shutdown_step_skips_standalone(app):
    from qubes_provider.client import shutdown_for_template_update
    vm = app.add_new_vm("StandaloneVM", "tf-sa", "blue")
    object.__setattr__(vm, "_running", True)
    shutdown_for_template_update(app, vm, {"template": "whatever"})
    assert vm.is_running()  # never shut down (StandaloneVM has no template)


# --- delete / import --------------------------------------------------------

def test_delete_removes_vm(app):
    app.add_new_vm("AppVM", "tf-work", "blue")
    res().delete(ctx(), make())
    assert "tf-work" not in app.domains


def test_import_existing(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    assert res().import_(ctx(), "tf-work")["name"] == "tf-work"


def test_import_missing(app):
    c = ctx()
    assert res().import_(c, "nope") is None
    c.diagnostics.add_error.assert_called_once()


# --- data source ------------------------------------------------------------

def test_data_source_reads_power_state_and_props(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    object.__setattr__(vm, "_running", True)
    state = QubesVMDataSource(provider=MagicMock()).read(
        ctx(), {"name": "tf-work", "properties": {"memory": "400"}})
    assert state["power_state"] == "Running"
    assert state["vm_class"] == "AppVM"
    assert state["properties"]["memory"] == "400"


# --- enforce typed error mapping --------------------------------------------

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
        enforce_properties(None, BoomVM(), {"memory": "99999"})
