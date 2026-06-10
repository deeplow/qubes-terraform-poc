# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the capabilities delegated to qubes-ansible and exposed in the
schema: clone, volumes, services, notes, devices, power state. Driven against
the real QubeModule via the ``qube_env`` fixture."""

from unittest.mock import MagicMock

from qubes_provider.resources.vm import QubesVMResource


def ctx():
    c = MagicMock()
    c.diagnostics.add_error = MagicMock()
    return c


def res():
    return QubesVMResource(provider=MagicMock())


def make(name, klass="AppVM", label="red", template=None, **extra):
    base = {"name": name, "klass": klass, "label": label, "template": template,
            "properties": None, "features": None, "tags": None,
            "shutdown_if_required": None, "force_shutdown": None,
            "clone_src": None, "volumes": None, "services": None,
            "notes": None, "devices": None}
    base.update(extra)
    return base


# --- clone ------------------------------------------------------------------

def test_create_via_clone_src(qube_env):
    c = ctx()
    state = res().create(c, make("tf-tmpl", klass="TemplateVM", clone_src="fedora-40"))
    c.diagnostics.add_error.assert_not_called()
    assert "tf-tmpl" in qube_env.domains
    assert qube_env.domains["tf-tmpl"].klass == "TemplateVM"
    assert state["clone_src"] == "fedora-40"


def test_clone_missing_source_errors(qube_env):
    c = ctx()
    assert res().create(c, make("tf-x", clone_src="does-not-exist")) is None
    c.diagnostics.add_error.assert_called_once()


# --- volumes ----------------------------------------------------------------

def test_volume_resize(qube_env):
    state = res().create(ctx(), make("tf-v", template="fedora-40",
                                     volumes={"private": {"size": "10737418240"}}))  # 10 GiB
    assert qube_env.domains["tf-v"].volumes["private"].size == 10 * 1024 ** 3
    assert state["volumes"]["private"]["size"] == "10737418240"   # grow-only echo


def test_volume_already_big_enough_roundtrips(qube_env):
    # default private is 1 GiB; asking for 1 GiB (in bytes) is a no-op and round-trips.
    state = res().create(ctx(), make("tf-v2", template="fedora-40",
                                     volumes={"private": {"size": "1073741824"}}))  # 1 GiB
    assert state["volumes"]["private"]["size"] == "1073741824"


def test_volume_revisions_to_keep_passthrough(qube_env):
    # revisions_to_keep is echoed from config (pass-through), not read back / transformed.
    state = res().create(ctx(), make("tf-v3", template="fedora-40",
                                     volumes={"private": {"revisions_to_keep": "3"}}))
    assert state["volumes"]["private"]["revisions_to_keep"] == "3"


# --- services ---------------------------------------------------------------

def test_services_enable_and_roundtrip(qube_env):
    state = res().create(ctx(), make("tf-s", services={"foo", "bar"}))
    vm = qube_env.domains["tf-s"]
    assert vm.features["service.foo"] == "1"
    assert vm.features["service.bar"] == "1"
    assert state["services"] == ["bar", "foo"]


def test_services_removal_converges(qube_env):
    res().create(ctx(), make("tf-s2", services={"foo"}))
    res().update(ctx(), make("tf-s2", services={"foo"}), make("tf-s2", services=set()))
    assert "service.foo" not in qube_env.domains["tf-s2"].features


def test_disabled_service_not_reported(qube_env):
    # A service feature set to "" exists but is disabled -> qube_facts reports it as
    # False, and to Terraform a disabled service does not exist.
    res().create(ctx(), make("tf-s3", services={"foo"}))
    qube_env.domains["tf-s3"].features["service.foo"] = ""   # disabled out of band
    state = res().read(ctx(), make("tf-s3", services={"foo"}))
    assert state["services"] == []


def test_services_block_deletion_removes(qube_env):
    # services is optional (not computed): deleting the block (-> None) disables the
    # declared service, and the undeclared bag reads back as None.
    res().create(ctx(), make("tf-s4", services={"foo"}))
    state = res().update(ctx(), make("tf-s4", services={"foo"}), make("tf-s4"))
    assert "service.foo" not in qube_env.domains["tf-s4"].features
    assert state["services"] is None


# --- notes ------------------------------------------------------------------

def test_notes_set_and_roundtrip(qube_env):
    state = res().create(ctx(), make("tf-n", notes="created by terraform"))
    assert qube_env.domains["tf-n"].get_notes() == "created by terraform"
    assert state["notes"] == "created by terraform"


# --- devices ----------------------------------------------------------------

def test_device_assignment(qube_env):
    devices = {"strategy": "strict", "items": ["block:sys-firewall:sda:0000"]}
    state = res().create(ctx(), make("tf-dev", template="fedora-40", devices=devices))
    coll = qube_env.domains["tf-dev"].devices["block"]
    assigned = coll.get_assigned_devices()
    assert len(assigned) == 1
    dev = assigned[0].virtual_device
    assert (dev.backend_domain, dev.port_id, dev.device_id) == ("sys-firewall", "sda", "0000")
    assert state["devices"] == devices


def test_device_flat_list_form(qube_env):
    state = res().create(ctx(), make("tf-dev2", template="fedora-40",
                                     devices=["block:sys-firewall:sdb:0001"]))
    assert len(qube_env.domains["tf-dev2"].devices["block"].get_assigned_devices()) == 1
    assert state["devices"] == ["block:sys-firewall:sdb:0001"]


# --- full state shape -------------------------------------------------------

def test_state_has_all_schema_attributes(qube_env):
    state = res().create(ctx(), make("tf-full", template="fedora-40"))
    schema_attrs = {a.name for a in QubesVMResource.get_schema().attributes}
    assert set(state) == schema_attrs   # every attribute is present in returned state
