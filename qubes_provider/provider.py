# SPDX-License-Identifier: GPL-3.0-or-later
"""The Qubes provider definition."""

from typing import Type

from tf import provider as p
from tf import schema
from tf.provider import DataSource, Resource
from tf.utils import Diagnostics

from .data_sources.system import (
    QubesDeviceClassDataSource,
    QubesLabelDataSource,
    QubesPoolDataSource,
    QubesPropertyDataSource,
    QubesVMClassDataSource,
)
from .resources.qubes_prefs import QubesPrefsResource
from .resources.vm import QubesVMResource


class QubesProvider(p.Provider):
    """Top-level provider wiring Qubes resources/data sources to the tf runtime."""

    def full_name(self) -> str:
        # host/namespace/type. Matches the `source` used in required_providers.
        return "registry.terraform.io/deeplow/qubes"

    def get_model_prefix(self) -> str:
        # Prepended to each resource/data source name -> "qubes_vm".
        return "qubes_"

    def get_provider_schema(self, diags: Diagnostics) -> schema.Schema:
        # No provider-level configuration in v1: qubesadmin.Qubes() auto-detects
        # its transport (local socket in dom0, qrexec from a management qube).
        return schema.Schema(attributes=[])

    def validate_config(self, diags: Diagnostics, config: dict):
        return

    def configure_provider(self, diags: Diagnostics, config: dict):
        return

    def get_resources(self) -> list[Type[Resource]]:
        return [QubesVMResource, QubesPrefsResource]

    def get_data_sources(self) -> list[Type[DataSource]]:
        return [
            QubesPropertyDataSource,
            QubesLabelDataSource,
            QubesPoolDataSource,
            QubesVMClassDataSource,
            QubesDeviceClassDataSource,
        ]
