# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only data sources for dom0 / system-wide Qubes info.

These read straight from ``qubesadmin`` (the Admin API) — global properties,
labels, storage pools, and the VM / device class catalogs. None concern a
concrete VM, and none touch qubes-ansible, so they live here rather than behind
the Terraform↔ansible adapter. The data source type names mirror the Admin API
namespaces: ``admin.property.*`` -> ``qubes_property``, ``admin.label.*`` ->
``qubes_label``, ``admin.pool.*`` -> ``qubes_pool``, ``admin.vmclass.*`` ->
``qubes_vmclass``, ``admin.deviceclass.*`` -> ``qubes_deviceclass``.
"""

from typing import Optional

import qubesadmin

from tf import schema, types
from tf.iface import ReadDataContext
from tf.provider import DataSource


def global_property_names() -> list:
    """dom0 global property names (``admin.property.List``)."""
    return sorted(qubesadmin.Qubes().property_list())


class QubesSystem:
    """Reads dom0 / system-wide info straight from qubesadmin (no VMs, no ansible).

    Injectable into the data sources (``backend=``) for tests."""

    def _app(self):
        return qubesadmin.Qubes()

    def read_property(self) -> dict:                # admin.property.List / .Get
        app = self._app()
        out = {}
        for name in sorted(app.property_list()):
            try:
                out[name] = self._str(getattr(app, name))
            except Exception:  # noqa: BLE001 - unset / unreadable -> null
                out[name] = None
        return out

    def read_label(self) -> dict:                   # admin.label.List / .Get
        app = self._app()
        return {"labels": {n: {"index": str(app.labels[n].index),
                               "color": app.labels[n].color}
                           for n in app.labels.keys()}}

    def read_pool(self) -> dict:                    # admin.pool.List / .Info / .ListDrivers
        app = self._app()
        pools = {}
        for n in app.pools.keys():
            pool = app.pools[n]
            attrs = {"driver": pool.driver, "usage": pool.usage, "size": pool.size}
            pools[n] = {k: str(v) for k, v in attrs.items() if v is not None}
        return {"pools": pools, "drivers": sorted(app.pool_drivers)}

    def read_vmclass(self) -> dict:                 # admin.vmclass.List
        return {"names": sorted(self._app().list_vmclass())}

    def read_deviceclass(self) -> dict:             # admin.deviceclass.List
        return {"names": sorted(self._app().list_deviceclass())}

    @staticmethod
    def _str(value) -> str:
        value = getattr(value, "name", value)       # VM-valued props -> name
        return "" if value is None else str(value)


class _SystemDataSource(DataSource):
    """Common plumbing: an injectable :class:`QubesSystem` backend + error wrap."""

    def __init__(self, provider, backend=None):
        self.provider = provider
        self.qubes = backend or QubesSystem()

    def _read(self, ctx, reader) -> Optional[dict]:
        try:
            return reader()
        except Exception as exc:  # noqa: BLE001 - e.g. QubesDaemonAccessError
            ctx.diagnostics.add_error("Failed to read Qubes system info", str(exc))
            return None


class QubesPropertyDataSource(_SystemDataSource):
    """dom0 global properties — one computed attribute per property (admin.property.*)."""

    @classmethod
    def get_name(cls) -> str:
        return "property"  # -> qubes_property

    @classmethod
    def get_schema(cls) -> schema.Schema:
        # Attributes are enumerated live from qubesadmin (admin.property.List), so
        # the schema never drifts from read_property(). Needs a reachable qubesd.
        return schema.Schema(attributes=[
            schema.Attribute(name, types.String(), computed=True)
            for name in global_property_names()
        ])

    def read(self, ctx: ReadDataContext, config: dict) -> Optional[dict]:
        return self._read(ctx, self.qubes.read_property)


class QubesLabelDataSource(_SystemDataSource):
    """Available labels (admin.label.*)."""

    @classmethod
    def get_name(cls) -> str:
        return "label"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(attributes=[
            schema.Attribute("labels", types.Map(types.Map(types.String())), computed=True,
                             description="Available labels: name -> { index, color }."),
        ])

    def read(self, ctx: ReadDataContext, config: dict) -> Optional[dict]:
        return self._read(ctx, self.qubes.read_label)


class QubesPoolDataSource(_SystemDataSource):
    """Storage pools and available drivers (admin.pool.*)."""

    @classmethod
    def get_name(cls) -> str:
        return "pool"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(attributes=[
            schema.Attribute("pools", types.Map(types.Map(types.String())), computed=True,
                             description="Storage pools: name -> { driver, usage, size } (bytes)."),
            schema.Attribute("drivers", types.Set(types.String()), computed=True,
                             description="Available storage-pool drivers."),
        ])

    def read(self, ctx: ReadDataContext, config: dict) -> Optional[dict]:
        return self._read(ctx, self.qubes.read_pool)


class QubesVMClassDataSource(_SystemDataSource):
    """Available VM classes (admin.vmclass.List)."""

    @classmethod
    def get_name(cls) -> str:
        return "vmclass"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(attributes=[
            schema.Attribute("names", types.Set(types.String()), computed=True,
                             description="Available VM classes (AppVM, TemplateVM, ...)."),
        ])

    def read(self, ctx: ReadDataContext, config: dict) -> Optional[dict]:
        return self._read(ctx, self.qubes.read_vmclass)


class QubesDeviceClassDataSource(_SystemDataSource):
    """Available device classes (admin.deviceclass.List)."""

    @classmethod
    def get_name(cls) -> str:
        return "deviceclass"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(attributes=[
            schema.Attribute("names", types.Set(types.String()), computed=True,
                             description="Available device classes (block, usb, pci, mic)."),
        ])

    def read(self, ctx: ReadDataContext, config: dict) -> Optional[dict]:
        return self._read(ctx, self.qubes.read_deviceclass)
