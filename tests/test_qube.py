# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the qubes_vm resource.

CRUD is delegated to the bundled qubes-ansible collection; these tests drive the
real QubeModule/qube_facts against in-memory fakes (the ``qube_env`` fixture) and
assert the provider's own glue: managed-subset reads, "*default*" round-trip,
declarative tag/feature removal, template-shutdown, create rollback, import.
"""

from unittest.mock import MagicMock

import qubesadmin.exc

from tests.fakes import FakeVM
from qubes_provider.utils.errors import QubesProviderError
from qubes_provider.provider import QubesProvider
from qubes_provider.utils.qubes_adapter import QubesVmAdapter
from qubes_provider.resources.qube import QubesVMResource


def ctx():
    c = MagicMock()
    c.diagnostics.add_error = MagicMock()
    return c


def res():
    return QubesVMResource(provider=MagicMock())


def make(name="tf-work", klass="AppVM", label="blue", template=None,
         properties=None, features=None, tags=None,
         shutdown_if_required=None, force_shutdown=None):
    return {"name": name, "klass": klass, "label": label, "template": template,
            "properties": properties, "features": features, "tags": tags,
            "shutdown_if_required": shutdown_if_required, "force_shutdown": force_shutdown}


# --- provider wiring --------------------------------------------------------

def test_resource_type_name_is_qubes_vm():
    p = QubesProvider()
    assert p.get_model_prefix() + QubesVMResource.get_name() == "qubes_vm"
    assert QubesVMResource in p.get_resources()
    # data sources are wired/asserted in test_system.py


def test_schemas_build():
    assert QubesVMResource.get_schema().to_pb() is not None


# --- create -----------------------------------------------------------------

def test_create_with_properties(qube_env):
    c = ctx()
    state = res().create(c, make(template="fedora-40",
                                 properties={"netvm": "", "memory": "2048"}))
    c.diagnostics.add_error.assert_not_called()
    assert "tf-work" in qube_env.domains
    assert state["label"] == "blue"
    assert state["template"] == "fedora-40"
    assert state["properties"]["netvm"] == ""      # explicit none round-trips ("None"->"" )
    assert state["properties"]["memory"] == "2048"


def test_create_default_template_roundtrips(qube_env):
    state = res().create(ctx(), make(name="tf-d", template="*default*"))
    assert state["template"] == "*default*"


def test_create_default_property_roundtrips(qube_env):
    state = res().create(ctx(), make(name="tf-n", properties={"netvm": "*default*"}))
    assert state["properties"]["netvm"] == "*default*"


def test_create_features_and_tags(qube_env):
    state = res().create(ctx(), make(name="tf-f",
                                     features={"service.foo": "1"},
                                     tags={"team-x"}))
    assert state["features"] == {"service.foo": "1"}
    assert state["tags"] == ["team-x"]
    assert qube_env.domains["tf-f"].features["service.foo"] == "1"
    assert "team-x" in qube_env.domains["tf-f"].tags


def test_create_reports_error(qube_env):
    # netvm pointing at a nonexistent qube fails qubes-ansible validation.
    c = ctx()
    assert res().create(c, make(name="bad", properties={"netvm": "ghost"})) is None
    c.diagnostics.add_error.assert_called_once()
    assert "bad" not in qube_env.domains


def test_create_rolls_back_on_enforce_failure(qube_env):
    # A backend that creates the qube then fails enforcement; the resource must
    # roll back via the (real) try_delete, which drives QubeModule against fakes.
    class BoomBackend(QubesVmAdapter):
        def create_or_update(self, desired, prior=None):
            qube_env.add_new_vm("AppVM", desired["name"], "red")  # partial create
            raise QubesProviderError("enforce boom")

    c = ctx()
    r = QubesVMResource(provider=MagicMock(), backend=BoomBackend())
    assert r.create(c, make(name="tf-rb")) is None
    assert "tf-rb" not in qube_env.domains            # rolled back
    c.diagnostics.add_error.assert_called_once()


# --- read -------------------------------------------------------------------

def test_read_missing_returns_none(qube_env):
    assert res().read(ctx(), make(name="ghost")) is None


def test_read_reports_error(qube_env):
    class BoomRead(QubesVmAdapter):
        def read_vm(self, name, desired):
            raise RuntimeError("Request refused")

    c = ctx()
    r = QubesVMResource(provider=MagicMock(), backend=BoomRead())
    assert r.read(c, make()) is None
    c.diagnostics.add_error.assert_called_once()


# --- update -----------------------------------------------------------------

def test_update_changes_property(qube_env):
    res().create(ctx(), make(template="fedora-40", properties={"memory": "400"}))
    state = res().update(ctx(),
                         make(properties={"memory": "400"}),
                         make(label="red", properties={"memory": "1024"}))
    assert state["label"] == "red"
    assert state["properties"]["memory"] == "1024"


def test_update_property_to_default(qube_env):
    res().create(ctx(), make(properties={"netvm": "sys-firewall"}))
    state = res().update(ctx(),
                         make(properties={"netvm": "sys-firewall"}),
                         make(properties={"netvm": "*default*"}))
    assert state["properties"]["netvm"] == "*default*"


def test_update_removes_dropped_feature_and_tag(qube_env):
    res().create(ctx(), make(features={"service.foo": "1"}, tags={"team-x"}))
    state = res().update(ctx(),
                         make(features={"service.foo": "1"}, tags={"team-x"}),
                         make(features={}, tags=set()))
    vm = qube_env.domains["tf-work"]
    assert "service.foo" not in vm.features
    assert "team-x" not in vm.tags
    assert state["tags"] == []


def test_tags_managed_subset_ignores_auto_tags(qube_env):
    res().create(ctx(), make(tags={"team-x"}))
    qube_env.domains["tf-work"].tags.add("created-by-vibe")   # auto-tag, not declared
    state = res().update(ctx(),
                         make(tags={"team-x"}),
                         make(tags=set()))
    assert state["tags"] == []                                # declared tag removed/absent
    assert "created-by-vibe" in qube_env.domains["tf-work"].tags  # auto-tag untouched


# --- config-authoritative bags: deleting the block removes declared keys -----

def test_features_block_deletion_removes(qube_env):
    # features is optional (not computed): deleting the whole block (-> None) converges
    # to removal, and the undeclared bag reads back as None.
    res().create(ctx(), make(features={"custom1": "1"}))
    state = res().update(ctx(),
                         make(features={"custom1": "1"}),
                         make())  # features omitted entirely -> None
    assert "custom1" not in qube_env.domains["tf-work"].features
    assert state["features"] is None


def test_features_managed_subset_ignores_system_features(qube_env):
    # Deleting the block removes only Terraform-tracked features; features the system
    # set (never in state) survive, because removal diffs prior state vs planned config.
    res().create(ctx(), make(features={"custom1": "1"}))
    qube_env.domains["tf-work"].features["gui"] = "1"   # system feature, not declared
    res().update(ctx(), make(features={"custom1": "1"}), make())  # delete block
    vm = qube_env.domains["tf-work"]
    assert "custom1" not in vm.features      # declared feature removed
    assert vm.features["gui"] == "1"         # system feature untouched


def test_tags_block_deletion_removes(qube_env):
    res().create(ctx(), make(tags={"team-x"}))
    state = res().update(ctx(), make(tags={"team-x"}), make())  # tags omitted -> None
    assert "team-x" not in qube_env.domains["tf-work"].tags
    assert state["tags"] is None


# --- shutdown-for-template-update (delegated to qubes-ansible) ---------------

def test_template_change_running_without_flag_errors(qube_env):
    res().create(ctx(), make(template="fedora-40"))
    qube_env.domains["tf-work"].start()
    c = ctx()
    out = res().update(c, make(template="fedora-40"),
                       make(template="default-template"))
    assert out is None
    c.diagnostics.add_error.assert_called_once()
    assert "running" in str(c.diagnostics.add_error.call_args).lower()
    assert qube_env.domains["tf-work"].is_running()  # not shut down


def test_template_change_running_with_flag_shuts_down(qube_env):
    res().create(ctx(), make(template="fedora-40"))
    qube_env.domains["tf-work"].start()
    state = res().update(ctx(), make(template="fedora-40"),
                         make(template="default-template", shutdown_if_required=True))
    assert qube_env.domains["tf-work"].is_halted()        # shut down first
    assert state["template"] == "default-template"
    assert state["shutdown_if_required"] is True          # flag echoed


def test_template_change_halted_succeeds(qube_env):
    res().create(ctx(), make(template="fedora-40"))
    state = res().update(ctx(), make(template="fedora-40"),
                         make(template="default-template"))
    assert state["template"] == "default-template"


# --- rename (clone + repoint dependents + remove) ---------------------------

def test_name_change_does_not_replace():
    attrs = {a.name: a for a in QubesVMResource.get_schema().attributes}
    assert not attrs["name"].requires_replace


def test_rename_keeps_qube(qube_env):
    res().create(ctx(), make(properties={"memory": "1024"}, tags={"team-x"}))
    qube_env.domains["tf-work"].features["gui"] = "1"   # undeclared: kept by the clone
    c = ctx()
    state = res().update(c, make(properties={"memory": "1024"}, tags={"team-x"}),
                         make(name="tf-new", properties={"memory": "1024"}, tags={"team-x"}))
    c.diagnostics.add_error.assert_not_called()
    assert "tf-work" not in qube_env.domains
    vm = qube_env.domains["tf-new"]
    assert vm.features["gui"] == "1"
    assert "team-x" in vm.tags
    assert state["name"] == "tf-new"
    assert state["properties"]["memory"] == "1024"


def test_rename_repoints_dependents(qube_env):
    res().create(ctx(), make(name="tf-app", template="fedora-40"))
    tpl = make(name="fedora-40", klass="TemplateVM", label="black")
    c = ctx()
    res().update(c, tpl, {**tpl, "name": "tf-tpl"})
    c.diagnostics.add_error.assert_not_called()
    assert "fedora-40" not in qube_env.domains
    assert qube_env.domains["tf-app"].template.name == "tf-tpl"
    assert qube_env.default_template.name == "tf-tpl"   # global setting too


def test_rename_refused_while_template_dependent_runs(qube_env):
    res().create(ctx(), make(name="tf-app", template="fedora-40"))
    qube_env.domains["tf-app"].start()
    tpl = make(name="fedora-40", klass="TemplateVM", label="black")
    c = ctx()
    assert res().update(c, tpl, {**tpl, "name": "tf-tpl"}) is None
    assert "tf-app" in str(c.diagnostics.add_error.call_args)
    assert "fedora-40" in qube_env.domains
    assert "tf-tpl" not in qube_env.domains


def test_rename_running_qube_without_flag_errors(qube_env):
    res().create(ctx(), make())
    qube_env.domains["tf-work"].start()
    c = ctx()
    assert res().update(c, make(), make(name="tf-new")) is None
    c.diagnostics.add_error.assert_called_once()
    assert qube_env.domains["tf-work"].is_running()
    assert "tf-new" not in qube_env.domains


def test_rename_running_qube_with_flag_shuts_down(qube_env):
    res().create(ctx(), make())
    old = qube_env.domains["tf-work"]
    old.start()
    state = res().update(ctx(), make(), make(name="tf-new", shutdown_if_required=True))
    assert old.is_halted()
    assert state["name"] == "tf-new"
    assert "tf-work" not in qube_env.domains


def test_rename_rolls_back_when_repoint_fails(qube_env, monkeypatch):
    res().create(ctx(), make(name="tf-app", template="fedora-40"))
    set_attr = FakeVM.__setattr__

    def refuse_template(vm, key, value):
        if key == "template":
            raise qubesadmin.exc.QubesException("refused")
        set_attr(vm, key, value)

    monkeypatch.setattr(FakeVM, "__setattr__", refuse_template)
    tpl = make(name="fedora-40", klass="TemplateVM", label="black")
    c = ctx()
    assert res().update(c, tpl, {**tpl, "name": "tf-tpl"}) is None
    assert "tf-tpl" not in qube_env.domains                   # clone removed
    assert qube_env.default_template.name == "fedora-40"      # global pointed back
    assert qube_env.domains["tf-app"].template.name == "fedora-40"


# --- delete / import --------------------------------------------------------

def test_delete_removes_vm(qube_env):
    res().create(ctx(), make())
    res().delete(ctx(), make())
    assert "tf-work" not in qube_env.domains


def test_import_existing(qube_env):
    res().create(ctx(), make(template="fedora-40"))
    state = res().import_(ctx(), "tf-work")
    assert state["name"] == "tf-work"
    assert state["klass"] == "AppVM"


def test_import_missing(qube_env):
    c = ctx()
    assert res().import_(c, "nope") is None
    c.diagnostics.add_error.assert_called_once()
