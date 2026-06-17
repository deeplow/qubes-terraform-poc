# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the qubes-ansible Adapter: the AnsibleModule stand-in, the base
declarative->Ansible transforms, the Terraform->qube parameter mapping, and that
bootstrap makes the bundled collection importable.

These exercise the pure pieces (no qubesadmin / no live Qubes)."""

import pytest

from tf.types import Unknown

from qubes_provider.utils.errors import QubesProviderError
from qubes_provider.utils.qubes_adapter import (
    AnsibleModuleShim,
    QubesVmAdapter,
    TerraformAnsibleAdapter,
)


def adapter():
    return QubesVmAdapter()


# --- AnsibleModuleShim (the AnsibleModule stand-in) -------------------------

def test_exit_json_captures_result():
    mod = AnsibleModuleShim({"name": "x"})
    assert mod.result is None
    mod.exit_json(changed=True, diff={"a": 1})
    assert mod.result == {"changed": True, "diff": {"a": 1}}


def test_fail_json_raises_with_msg():
    mod = AnsibleModuleShim({})
    with pytest.raises(QubesProviderError, match="boom"):
        mod.fail_json(msg="boom")


def test_fail_json_without_msg_still_raises():
    mod = AnsibleModuleShim({})
    with pytest.raises(QubesProviderError):
        mod.fail_json(other="info")


# --- base declarative -> Ansible transforms ---------------------------------

def test_value_transforms():
    A = TerraformAnsibleAdapter
    assert A.is_concrete("x") is True
    assert A.is_concrete(None) is False
    assert A.is_concrete(Unknown) is False
    assert A.as_dict(None) == {}
    assert A.as_dict({"a": 1}) == {"a": 1}
    assert A.as_set(None) == set()
    assert A.as_set(["a", "b"]) == {"a", "b"}


def test_removed_items_handles_sets_and_dicts():
    A = TerraformAnsibleAdapter
    # set-valued keys (tags/services)
    assert A.removed_items({"tags": {"a", "b"}}, {"tags": {"a"}}, "tags") == {"b"}
    # dict-valued keys (features) -> compares keys
    assert A.removed_items(
        {"features": {"x": "1", "y": "1"}}, {"features": {"x": "1"}}, "features"
    ) == {"y"}
    # tolerant of missing / None
    assert A.removed_items({}, {"tags": None}, "tags") == set()


def test_to_params_schema_driven():
    from tf import types

    A = TerraformAnsibleAdapter()
    types_by_name = {
        "tags": types.Set(types.String()),       # Set -> sorted, de-duped list
        "properties": types.Map(types.String()),  # Map -> dict
        "force_shutdown": types.Bool(),            # Bool -> bool, renamed to "force"
        "notes": types.String(),                   # String -> passthrough / None
        "devices": types.NormalizedJson(),         # raw -> passthrough / None
        "label": types.String(),                   # skipped
    }
    desired = {
        "tags": ["b", "a", "a"], "properties": {"netvm": ""},
        "force_shutdown": True, "notes": "hi", "devices": {"x": 1}, "label": "red",
    }
    p = A.to_params(desired, types_by_name,
                    renames={"force_shutdown": "force"}, skip={"label"})
    assert p == {"tags": ["a", "b"], "properties": {"netvm": ""},
                 "force": True, "notes": "hi", "devices": {"x": 1}}
    assert "label" not in p and "force_shutdown" not in p

    # Terraform null / unknown -> the per-type default.
    empty = A.to_params({}, types_by_name, renames={"force_shutdown": "force"}, skip={"label"})
    assert empty == {"tags": [], "properties": {}, "force": False,
                     "notes": None, "devices": None}
    unknown = A.to_params(
        {k: Unknown for k in types_by_name}, types_by_name,
        renames={"force_shutdown": "force"}, skip={"label"},
    )
    assert unknown == empty


# --- desired_to_qube_params translation -------------------------------------

def test_mapping_folds_label_into_properties_and_maps_class():
    p = adapter().desired_to_qube_params({
        "name": "tf-x", "klass": "AppVM", "label": "red",
        "properties": {"netvm": ""},
    })
    assert p["name"] == "tf-x"
    assert p["klass"] == "AppVM"
    assert p["properties"] == {"netvm": "", "label": "red"}
    assert p["force"] is False              # default from absent force_shutdown
    assert p["state"] == "present"          # the one required default


def test_mapping_flags():
    p = adapter().desired_to_qube_params({
        "name": "x", "klass": "AppVM", "label": "red",
        "shutdown_if_required": True, "force_shutdown": True,
    })
    assert p["shutdown_if_required"] is True
    assert p["force"] is True
    assert p["state"] == "present"          # no power management; always ensure-present


def test_mapping_unset_optionals_use_type_defaults():
    # Every param is present with its TfType-appropriate default (Terraform null ==
    # "use default"): Map -> {}, Set -> [], Bool -> False, String/json -> None.
    p = adapter().desired_to_qube_params({"name": "x", "klass": "AppVM", "label": "red"})
    assert p["properties"] == {"label": "red"}   # label folded into the (empty) Map
    assert p["features"] == {}
    assert p["volumes"] == {}
    assert p["services"] == []
    assert p["tags"] == []
    assert "clone_src" not in p          # only added by an origin of type "clone"
    assert p["notes"] is None
    assert p["devices"] is None
    assert p["template"] is None
    assert p["force"] is False
    assert p["shutdown_if_required"] is False
    assert p["state"] == "present"


def test_mapping_volumes_passthrough():
    # Volumes match qubes-ansible's param shape (size in bytes); passed through as-is.
    p = adapter().desired_to_qube_params({
        "name": "x", "klass": "AppVM", "label": "red",
        "volumes": {"private": {"size": "10737418240", "revisions_to_keep": "3"}},
    })
    assert p["volumes"] == {"private": {"size": "10737418240", "revisions_to_keep": "3"}}


def test_mapping_default_template_becomes_none():
    # "*default*" template -> None (use qubesd default; never Reset the template).
    p = adapter().desired_to_qube_params({"name": "x", "klass": "AppVM", "label": "red",
                                          "template": "*default*"})
    assert p["template"] is None


def test_mapping_keeps_is_concrete_template():
    p = adapter().desired_to_qube_params({"name": "x", "klass": "AppVM", "label": "red",
                                          "template": "fedora-40"})
    assert p["template"] == "fedora-40"


def test_mapping_passes_devices_raw_and_origin_clone():
    devices = {"strategy": "strict", "items": ["block:sys-usb:sda:0000"]}
    p = adapter().desired_to_qube_params({
        "name": "x", "klass": "AppVM", "label": "red",
        "origin": {"type": "clone", "name": "fedora-40"}, "devices": devices,
    })
    assert p["clone_src"] == "fedora-40"     # origin clone -> qubes-ansible clone_src
    assert p["devices"] == devices


def test_mapping_repo_origin_adds_no_clone_src():
    # A "repo" origin is installed via qvm-template (not qubes-ansible), so it
    # contributes no QubeModule param.
    p = adapter().desired_to_qube_params({
        "name": "debian-12-minimal", "klass": "TemplateVM", "label": "black",
        "origin": {"type": "repo", "name": "debian-12-minimal"},
    })
    assert "clone_src" not in p


# --- origin validation ------------------------------------------------------

def test_validate_origin_rejects_bad_type():
    with pytest.raises(QubesProviderError, match="origin.type"):
        adapter()._validate_origin({"name": "x", "origin": {"type": "bogus", "name": "y"}})


def test_validate_origin_requires_name():
    with pytest.raises(QubesProviderError, match="origin.name"):
        adapter()._validate_origin({"name": "x", "origin": {"type": "clone"}})


def test_validate_origin_rejects_repo_keys_with_clone():
    with pytest.raises(QubesProviderError, match="repo_"):
        adapter()._validate_origin(
            {"name": "x", "origin": {"type": "clone", "name": "y", "repo_id": "r"}})


def test_validate_origin_repo_requires_name_match():
    with pytest.raises(QubesProviderError, match="must equal origin.name"):
        adapter()._validate_origin(
            {"name": "x", "origin": {"type": "repo", "name": "debian-12-minimal"}})


# --- bootstrap makes the vendored modules importable ------------------------

def test_bootstrap_imports_qube_module_and_facts():
    from qubes_provider.utils import qubes_adapter
    qubes_adapter._bootstrap()
    import importlib
    qm = importlib.import_module("qubes_module_qube")
    qf = importlib.import_module("qube_facts")
    assert hasattr(qm, "QubeModule")
    assert hasattr(qf, "core")
    # The qube modules must import with qubesadmin intact (not None) — on 4.2 this
    # only holds because the compat shim provides qubesadmin.device_protocol.
    assert qm.qubesadmin is not None


def test_compat_shim_fills_4_2_gaps():
    # After bootstrap, the 4.3 APIs qubes-ansible needs must be present (real on 4.3,
    # shimmed on 4.2). qubesadmin is a system package; skip if it isn't installed.
    from qubes_provider.utils import qubes_adapter
    qubes_adapter._bootstrap()
    import importlib
    try:
        importlib.import_module("qubesadmin.device_protocol")
    except ImportError:
        pytest.skip("qubesadmin not installed")
    import qubesadmin.vm
    import qubesadmin.devices
    assert hasattr(qubesadmin.vm.QubesVM, "get_notes")
    assert hasattr(qubesadmin.devices.DeviceCollection, "get_assigned_devices")
