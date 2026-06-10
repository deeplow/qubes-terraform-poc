# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the dom0 / system-wide data sources (qubes_property/label/pool/
vmclass/deviceclass). The real ``QubesSystem`` read logic runs against the
in-memory ``FakeApp`` by pointing the module's ``qubesadmin`` at it."""

from unittest.mock import MagicMock

import pytest

from qubes_provider.data_sources import system
from qubes_provider.data_sources.system import (
    QubesDeviceClassDataSource,
    QubesLabelDataSource,
    QubesPoolDataSource,
    QubesPropertyDataSource,
    QubesVMClassDataSource,
)
from qubes_provider.provider import QubesProvider

ALL = (QubesPropertyDataSource, QubesLabelDataSource, QubesPoolDataSource,
       QubesVMClassDataSource, QubesDeviceClassDataSource)


def ctx():
    c = MagicMock()
    c.diagnostics.add_error = MagicMock()
    return c


@pytest.fixture
def sys_env(monkeypatch, fake_app):
    """Point the data-source module's qubesadmin at the FakeApp."""
    class _QubesShim:
        @staticmethod
        def Qubes():
            return fake_app

    monkeypatch.setattr(system, "qubesadmin", _QubesShim)
    return fake_app


def ds(cls):
    return cls(provider=MagicMock())


# --- qubes_property ---------------------------------------------------------

def test_property_typed_attrs_resolve(sys_env):
    state = ds(QubesPropertyDataSource).read(ctx(), {})
    assert state["default_template"] == "fedora-40"     # VM-valued -> name
    assert state["default_netvm"] == "sys-firewall"
    assert state["default_qrexec_timeout"] == "60"      # stringified
    assert state["default_dispvm"] == ""


def test_property_schema_is_dynamic(sys_env):
    # Schema attributes are built from qubesadmin.property_list() and match the read.
    attrs = {a.name for a in QubesPropertyDataSource.get_schema().attributes}
    assert attrs == set(sys_env.property_list())
    assert set(ds(QubesPropertyDataSource).read(ctx(), {})) == attrs


# --- qubes_label / qubes_pool ----------------------------------------------

def test_labels(sys_env):
    labels = ds(QubesLabelDataSource).read(ctx(), {})["labels"]
    assert labels["red"]["color"] == "0xcc0000"
    assert labels["red"]["index"] == "1"


def test_pools(sys_env):
    state = ds(QubesPoolDataSource).read(ctx(), {})
    assert state["pools"]["vm-pool"]["driver"] == "lvm_thin"
    assert "lvm_thin" in state["drivers"]
    assert "usage" not in state["pools"]["linux-kernel"]   # None attrs omitted


# --- qubes_vmclass / qubes_deviceclass -------------------------------------

def test_vmclass_and_deviceclass(sys_env):
    assert "AppVM" in ds(QubesVMClassDataSource).read(ctx(), {})["names"]
    assert "block" in ds(QubesDeviceClassDataSource).read(ctx(), {})["names"]


# --- wiring / schema --------------------------------------------------------

def test_schemas_build(sys_env):
    for cls in ALL:
        assert cls.get_schema().to_pb() is not None


def test_provider_wires_all_data_sources():
    got = QubesProvider().get_data_sources()
    for cls in ALL:
        assert cls in got


def test_read_reports_error(sys_env):
    class Boom(system.QubesSystem):
        def read_pool(self):
            raise RuntimeError("Request refused")

    c = ctx()
    assert QubesPoolDataSource(provider=MagicMock(), backend=Boom()).read(c, {}) is None
    c.diagnostics.add_error.assert_called_once()
