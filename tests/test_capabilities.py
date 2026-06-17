# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the capabilities delegated to qubes-ansible and exposed in the
schema: clone, volumes, services, notes, devices, power state. Driven against
the real QubeModule via the ``qube_env`` fixture."""

from unittest.mock import MagicMock

from qubes_provider.resources.qube import QubesVMResource


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
            "origin": None, "volumes": None, "services": None,
            "notes": None, "devices": None}
    base.update(extra)
    return base


# --- origin: clone ----------------------------------------------------------

def test_create_via_origin_clone(qube_env):
    c = ctx()
    state = res().create(c, make("tf-tmpl", klass="TemplateVM",
                                 origin={"type": "clone", "name": "fedora-40"}))
    c.diagnostics.add_error.assert_not_called()
    assert "tf-tmpl" in qube_env.domains
    assert qube_env.domains["tf-tmpl"].klass == "TemplateVM"
    assert state["origin"] == {"type": "clone", "name": "fedora-40"}


def test_clone_missing_source_errors(qube_env):
    c = ctx()
    assert res().create(
        c, make("tf-x", origin={"type": "clone", "name": "does-not-exist"})) is None
    c.diagnostics.add_error.assert_called_once()


# --- origin: repo (qvm-template install) ------------------------------------

def test_create_via_origin_repo_installs_when_absent(qube_env, monkeypatch):
    import qubesadmin.tools.qvm_template as qt
    from qubes_provider.utils import qubes_adapter as adapter
    from tests.fakes import FakeVM

    calls = []

    def fake_main(argv, app=None):
        # Validate argv against the REAL parser (qvm_template.main's two-pass logic)
        # so a malformed/mis-ordered argv can never slip through the stub again.
        p, rest = qt.parser.parse_known_args(argv)
        p = qt.parser.parse_args(rest, p)
        assert p.command == "install"
        assert p.templates == ["debian-12-minimal"]
        assert p.updatevm == ""
        assert p.repos == [("repoid", "qubes-templates-itl")]
        calls.append(argv)
        app.domains.add(FakeVM("debian-12-minimal", "TemplateVM", "black"))
        return 0

    monkeypatch.setattr(adapter.qvm_template, "main", fake_main)
    c = ctx()
    state = res().create(c, make("debian-12-minimal", klass="TemplateVM", label="black",
                                 origin={"type": "repo", "name": "debian-12-minimal",
                                         "repo_id": "qubes-templates-itl"}))
    c.diagnostics.add_error.assert_not_called()
    # Global options (--quiet/--updatevm/--repoid) precede the `install` subcommand.
    assert calls == [["--quiet", "--updatevm", "", "--repoid",
                      "qubes-templates-itl", "install", "debian-12-minimal"]]
    assert "debian-12-minimal" in qube_env.domains
    assert state["origin"]["type"] == "repo"


def test_create_via_origin_repo_pool_argv_parses(qube_env, monkeypatch):
    # repo_pool -> --pool, which IS an `install`-subcommand option (stays after `install`).
    import qubesadmin.tools.qvm_template as qt
    from qubes_provider.utils import qubes_adapter as adapter
    from tests.fakes import FakeVM

    calls = []

    def fake_main(argv, app=None):
        p, rest = qt.parser.parse_known_args(argv)
        p = qt.parser.parse_args(rest, p)
        assert p.command == "install"
        assert p.templates == ["debian-12-minimal"]
        assert p.updatevm == ""
        assert p.pool == "vm-pool"
        calls.append(argv)
        app.domains.add(FakeVM("debian-12-minimal", "TemplateVM", "black"))
        return 0

    monkeypatch.setattr(adapter.qvm_template, "main", fake_main)
    c = ctx()
    res().create(c, make("debian-12-minimal", klass="TemplateVM", label="black",
                         origin={"type": "repo", "name": "debian-12-minimal",
                                 "repo_pool": "vm-pool"}))
    c.diagnostics.add_error.assert_not_called()
    assert calls == [["--quiet", "--updatevm", "", "install", "--pool",
                      "vm-pool", "debian-12-minimal"]]


def test_create_via_origin_repo_argparse_error_fails_resource(qube_env, monkeypatch):
    # A SystemExit from qvm-template's argparse must surface as a resource error,
    # not escape as BaseException (which would hang the apply).
    from qubes_provider.utils import qubes_adapter as adapter

    def fake_main(argv, app=None):
        raise SystemExit(2)

    monkeypatch.setattr(adapter.qvm_template, "main", fake_main)
    c = ctx()
    assert res().create(c, make("debian-12-minimal", klass="TemplateVM", label="black",
                                origin={"type": "repo", "name": "debian-12-minimal"})) is None
    c.diagnostics.add_error.assert_called_once()


def test_create_via_origin_repo_idempotent_when_present(qube_env, monkeypatch):
    from qubes_provider.utils import qubes_adapter as adapter

    called = []
    monkeypatch.setattr(adapter.qvm_template, "main",
                        lambda argv, app=None: called.append(argv) or 0)
    # fedora-40 already exists in the fake app -> no repo fetch.
    res().create(ctx(), make("fedora-40", klass="TemplateVM", label="black",
                             origin={"type": "repo", "name": "fedora-40"}))
    assert called == []


def test_origin_does_not_force_replacement():
    # origin is creation-time only and not reconstructable from a live qube, so it
    # must NOT be requires_replace -- otherwise an imported qube (origin reads back
    # null) is force-recreated against its config. Re-clone-on-source-change is
    # expressed in config via lifecycle.replace_triggered_by instead.
    origin = {a.name: a for a in QubesVMResource.get_schema().attributes}["origin"]
    assert not origin.requires_replace


def test_update_with_origin_clone_does_not_reclone(qube_env):
    # An existing qube carrying an origin clone spec (e.g. just after import) is
    # updated in place: the clone runs only when the qube is absent, so update must
    # neither error nor re-clone over the live qube.
    res().create(ctx(), make("tf-up", klass="TemplateVM",
                             origin={"type": "clone", "name": "fedora-40"}))
    before = qube_env.domains["tf-up"]
    c = ctx()
    state = res().update(c, make("tf-up", klass="TemplateVM",
                                 origin={"type": "clone", "name": "fedora-40"}),
                         make("tf-up", klass="TemplateVM", label="green",
                              origin={"type": "clone", "name": "fedora-40"}))
    c.diagnostics.add_error.assert_not_called()
    assert qube_env.domains["tf-up"] is before          # not re-created
    assert state["origin"] == {"type": "clone", "name": "fedora-40"}


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
