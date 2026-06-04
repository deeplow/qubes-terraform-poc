"""Unit tests for the qubes_vm resource and data source (no real Qubes needed)."""

from unittest.mock import MagicMock

import pytest

from qubes_provider import client
from qubes_provider.data_sources.vm import QubesVMDataSource
from qubes_provider.provider import QubesProvider
from qubes_provider.resources.vm import QubesVMResource
from tests.fakes import FakeApp


@pytest.fixture
def app(monkeypatch):
    """Patch get_app() everywhere it's imported to return a shared FakeApp."""
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


def test_schema_builds():
    # to_pb() exercises every attribute/type mapping end-to-end.
    assert QubesVMResource.get_schema().to_pb() is not None
    assert QubesVMDataSource.get_schema().to_pb() is not None


# --- CRUD -------------------------------------------------------------------

def test_create_appvm(app):
    c = ctx()
    planned = {
        "name": "tf-work", "vm_class": "AppVM", "label": "blue",
        "template": "fedora-40", "memory": 2048, "maxmem": None, "netvm": None,
    }
    state = res().create(c, planned)
    c.diagnostics.add_error.assert_not_called()
    assert "tf-work" in app.domains
    assert state["name"] == "tf-work"
    assert state["vm_class"] == "AppVM"
    assert state["label"] == "blue"
    assert state["template"] == "fedora-40"
    assert state["memory"] == 2048
    assert state["provisioned"] is True


def test_create_reports_error_on_failure(app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("name already used")

    monkeypatch.setattr(app, "add_new_vm", boom)
    c = ctx()
    state = res().create(c, {"name": "dup", "vm_class": "AppVM", "label": "red"})
    assert state is None
    c.diagnostics.add_error.assert_called_once()


def test_read_missing_returns_none(app):
    assert res().read(ctx(), {"name": "ghost"}) is None


def test_read_reports_daemon_access_error(app, monkeypatch):
    # Simulate qubesadmin raising (e.g. QubesDaemonAccessError: policy denied)
    # instead of crashing the plugin.
    class Boom:
        def __contains__(self, key):
            raise RuntimeError("Request refused")

    monkeypatch.setattr(app, "domains", Boom())
    c = ctx()
    assert res().read(c, {"name": "tf-work"}) is None
    c.diagnostics.add_error.assert_called_once()


def test_read_existing(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    state = res().read(ctx(), {"name": "tf-work"})
    assert state["name"] == "tf-work"
    assert state["template"] == "fedora-40"


def test_update_changes_label_and_memory(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    current = {"name": "tf-work", "label": "blue", "memory": 400}
    planned = {"name": "tf-work", "label": "red", "memory": 1024}
    state = res().update(ctx(), current, planned)
    assert state["label"] == "red"
    assert state["memory"] == 1024
    assert app.domains["tf-work"]._label == "red"


def test_update_only_writes_changed_concrete_values(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue")
    vm.netvm = "sys-firewall"
    # netvm unchanged + memory is Unknown (computed, not set) -> no writes
    from tf.types import Unknown
    current = {"name": "tf-work", "label": "blue", "netvm": "sys-firewall"}
    planned = {"name": "tf-work", "label": "blue", "netvm": "sys-firewall",
               "memory": Unknown}
    res().update(ctx(), current, planned)
    assert app.domains["tf-work"].memory == 400  # untouched


def test_create_with_default_template_roundtrips(app):
    # template="@default" -> created with default template -> reads back "@default".
    # Regression: qubesd pins template to the default *value*, so the value (not
    # property_is_default) must drive the round-trip via GetDefault.
    c = ctx()
    planned = {"name": "tf-d", "vm_class": "AppVM", "label": "red",
               "template": "@default", "netvm": "@default"}
    state = res().create(c, planned)
    c.diagnostics.add_error.assert_not_called()
    assert state["template"] == "@default"   # not the concrete default-template name
    assert state["netvm"] == "@default"
    # template is pinned (NOT default-following), yet still round-trips to "@default".
    assert app.domains["tf-d"].property_is_default("template") is False
    assert app.domains["tf-d"].template == "default-template"


def test_create_default_template_with_drift_reads_concrete(app):
    # If the live value no longer equals the default, read returns the concrete name.
    res().create(ctx(), {"name": "tf-d2", "vm_class": "AppVM", "label": "red",
                         "template": "@default"})
    app.domains["tf-d2"].template = "fedora-40"  # drift away from default
    state = res().read(ctx(), {"name": "tf-d2", "template": "@default"})
    assert state["template"] == "fedora-40"


def test_create_explicit_no_netvm_roundtrips(app):
    state = res().create(ctx(), {"name": "tf-n", "vm_class": "AppVM",
                                 "label": "red", "netvm": ""})
    assert state["netvm"] == ""  # explicit none, distinct from "@default"
    assert app.domains["tf-n"].property_is_default("netvm") is False


def test_update_to_default_resets(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    vm.netvm = "sys-firewall"
    current = {"name": "tf-work", "label": "blue", "netvm": "sys-firewall"}
    planned = {"name": "tf-work", "label": "blue", "netvm": "@default"}
    state = res().update(ctx(), current, planned)
    assert app.domains["tf-work"].property_is_default("netvm") is True
    assert state["netvm"] == "@default"


def test_update_default_to_default_is_noop(app, monkeypatch):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    # netvm "@default" unchanged -> apply must write nothing.
    calls = []
    monkeypatch.setattr(client, "_set_prop",
                        lambda *a, **k: calls.append(a[2]))
    current = {"name": "tf-work", "label": "blue", "netvm": "@default"}
    planned = {"name": "tf-work", "label": "blue", "netvm": "@default"}
    res().update(ctx(), current, planned)
    assert "netvm" not in calls


def test_set_template_for_dispvms(app):
    state = res().create(ctx(), {"name": "tf-dt", "vm_class": "AppVM", "label": "red",
                                 "template_for_dispvms": True})
    assert state["template_for_dispvms"] is True
    assert app.domains["tf-dt"].template_for_dispvms is True


def test_template_for_dispvms_defaults_false_when_omitted(app, monkeypatch):
    from tf.types import Unknown
    calls = []
    monkeypatch.setattr(client, "_set_prop", lambda *a, **k: calls.append(a[2]))
    res().create(ctx(), {"name": "tf-x", "vm_class": "AppVM", "label": "red",
                         "template_for_dispvms": Unknown})
    assert "template_for_dispvms" not in calls  # computed/omitted -> not written


def test_create_dispvm_on_appvm_template(app):
    app.add_new_vm("AppVM", "tf-dt", "red")  # the dispvm template
    state = res().create(ctx(), {"name": "tf-disp", "vm_class": "DispVM",
                                 "label": "green", "template": "tf-dt"})
    assert state["vm_class"] == "DispVM"
    assert state["template"] == "tf-dt"


def test_delete_removes_vm(app):
    app.add_new_vm("AppVM", "tf-work", "blue")
    res().delete(ctx(), {"name": "tf-work"})
    assert "tf-work" not in app.domains


def test_delete_kills_running_vm_first(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue")
    vm._running = True
    vm.kill = MagicMock(side_effect=lambda: setattr(vm, "_running", False))
    res().delete(ctx(), {"name": "tf-work"})
    vm.kill.assert_called_once()
    assert "tf-work" not in app.domains


def test_import_existing(app):
    app.add_new_vm("AppVM", "tf-work", "blue", template="fedora-40")
    state = res().import_(ctx(), "tf-work")
    assert state["name"] == "tf-work"


def test_import_missing(app):
    c = ctx()
    assert res().import_(c, "nope") is None
    c.diagnostics.add_error.assert_called_once()


# --- data source ------------------------------------------------------------

def test_data_source_reads_power_state(app):
    vm = app.add_new_vm("AppVM", "tf-work", "blue")
    vm._running = True
    state = QubesVMDataSource(provider=MagicMock()).read(ctx(), {"name": "tf-work"})
    assert state["power_state"] == "Running"
    assert state["vm_class"] == "AppVM"


def test_data_source_missing(app):
    c = ctx()
    assert QubesVMDataSource(provider=MagicMock()).read(c, {"name": "ghost"}) is None
    c.diagnostics.add_error.assert_called_once()
