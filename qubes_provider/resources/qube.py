# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``qubes_vm`` managed resource: full CRUD for a Qubes VM.

CRUD is delegated to the bundled qubes-ansible collection via the
:class:`~qubes_provider.qubes_adapter.QubesVmAdapter` (the seam). This resource
only adds Terraform's model on top: roll back a failed create and echo the
behavioral flags into state — the adapter handles translation, projection onto the
declared subset, and converging removals.
"""

from typing import Optional

from tf import schema, types
from tf.iface import (
    CreateContext,
    DeleteContext,
    ImportContext,
    ReadContext,
    UpdateContext,
)
from tf.provider import Resource

from ..utils.qubes_adapter import QubesVmAdapter


class QubesVMResource(Resource):
    """Maps a Terraform resource onto a Qubes domain, via the QubesVmAdapter.

      create/update -> qubes.create_or_update
      read          -> qubes.read_vm
      delete        -> qubes.delete
    """

    def __init__(self, provider, backend=None):
        self.provider = provider
        self.qubes = backend or QubesVmAdapter()  # injectable for tests

    @classmethod
    def get_name(cls) -> str:
        # Combined with the provider model prefix "qubes_" -> "qubes_vm".
        return "vm"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(
            version=1,
            attributes=[
                # Identity. A Qubes VM name is unique and immutable as an
                # identity, so changing it replaces the resource.
                schema.Attribute(
                    "name", types.String(), required=True, requires_replace=True,
                    description="Unique Qubes VM name.",
                ),
                # VM class cannot be changed in place.
                schema.Attribute(
                    "klass", types.String(), required=True, requires_replace=True,
                    description="Qubes VM class: AppVM, TemplateVM, StandaloneVM, DispVM.",
                ),
                schema.Attribute(
                    "label", types.String(), required=True,
                    description="Label color, e.g. red, blue, green.",
                ),
                schema.Attribute(
                    "template", types.String(), optional=True, computed=True,
                    description=(
                        'Template to base this VM on. "*default*" uses the Qubes '
                        "default template; a name uses that template."
                    ),
                ),
                # Generic bags: any qube property/feature/tag, passed through
                # qubes-ansible and validated by qubesd. Values are strings;
                # "*default*" means the property's current Qubes default.
                schema.Attribute(
                    "properties", types.Map(types.String()),
                    optional=True, computed=True,
                    description=(
                        "Qube properties (memory, maxmem, netvm, kernel, virt_mode, "
                        "autostart, template_for_dispvms, guivm, ...). Any qubesd "
                        'property; values are strings; "*default*" = Qubes default.'
                    ),
                ),
                schema.Attribute(
                    "features", types.Map(types.String()),
                    optional=True,
                    description=(
                        "Qube features (vm.features); string values. Config-authoritative: "
                        "deleting/clearing the block removes the declared keys; system-set "
                        "features are never touched."
                    ),
                ),
                schema.Attribute(
                    "tags", types.Set(types.String()),
                    optional=True,
                    description=(
                        "Tags this resource manages. Config-authoritative: deleting/clearing "
                        "the block removes the declared tags; Qubes auto-tags are never touched."
                    ),
                ),
                # Behavioral flags (mirror qubes-ansible), not qube properties.
                schema.Attribute(
                    "shutdown_if_required", types.Bool(), optional=True,
                    description=(
                        "If a template change needs the qube halted and it is running, "
                        "shut it down first (default false -> error instead)."
                    ),
                ),
                schema.Attribute(
                    "force_shutdown", types.Bool(), optional=True,
                    description="Force the shutdown done for a template change.",
                ),
                # --- capabilities delegated to qubes-ansible ----------------
                schema.Attribute(
                    "origin", types.Map(types.String()),
                    optional=True,
                    description=(
                        "How this qube comes into being (creation-time identity). A flat "
                        'string map. Keys: "type" = "clone" | "repo"; "name" = the source '
                        "(clone: the qube to clone from; repo: the template to install). "
                        'For "repo": optional "repo_id" (qvm-template --repoid) and '
                        '"repo_pool" (--pool); idempotent — if a template named "name" '
                        "already exists it is used as-is, else installed via qvm-template. "
                        'For "repo", the resource\'s "name" must equal origin "name". '
                        "Creation-time only: it is consumed when the qube is first created "
                        "and is not reconstructable from a live qube, so an imported qube "
                        "reads it back as null and the first apply sets it in place (no "
                        "re-clone). It does not force replacement — to rebuild a qube when "
                        "its source changes, declare a lifecycle.replace_triggered_by on the "
                        "source resource in your config."
                    ),
                ),
                schema.Attribute(
                    "volumes", types.Map(types.Map(types.String())),
                    optional=True, computed=True,
                    description=(
                        'Volume config, e.g. { private = { size = "5368709120" } }. '
                        "Size is in bytes (matching qubes-ansible); volumes are grow-only."
                    ),
                ),
                schema.Attribute(
                    "services", types.Set(types.String()),
                    optional=True,
                    description=(
                        'Qubes services to enable (sets feature "service.<x>"). '
                        "Config-authoritative: deleting/clearing the block disables the "
                        "declared services."
                    ),
                ),
                schema.Attribute(
                    "notes", types.String(), optional=True, computed=True,
                    description="Free-form qube notes.",
                ),
                schema.Attribute(
                    "devices", types.NormalizedJson(), optional=True,
                    description=(
                        "Device assignments (raw qubes-ansible form): a list of "
                        '"class:backend:port:devid" specs, or {strategy, items}.'
                    ),
                ),
            ],
        )

    # --- CRUD ---------------------------------------------------------------

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        name = planned["name"]
        try:
            self.qubes.create_or_update(planned)
        except Exception as exc:  # noqa: BLE001
            self.qubes.try_delete(name)  # roll back so a retry isn't blocked
            ctx.diagnostics.add_error("Failed to create qube", f"{name}: {exc}")
            return None
        try:
            return self.qubes.read_vm(name, planned)
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to read qube", f"{name}: {exc}")
            return None

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
        try:
            return self.qubes.read_vm(current["name"], current)
        except Exception as exc:  # noqa: BLE001
            # e.g. QubesDaemonAccessError when qrexec policy denies access.
            ctx.diagnostics.add_error("Failed to read qube", str(exc))
            return None

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> Optional[dict]:
        try:
            self.qubes.create_or_update(planned, prior=current)
            return self.qubes.read_vm(planned["name"], planned)
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to update qube", str(exc))
            return None

    def delete(self, ctx: DeleteContext, current: dict):
        try:
            self.qubes.delete(current["name"])
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to delete qube", str(exc))

    def import_(self, ctx: ImportContext, id: str) -> Optional[dict]:
        """Adopt an existing qube into Terraform state by name."""
        try:
            state = self.qubes.read_vm(id, {})
            if state is None:
                ctx.diagnostics.add_error("No such qube", f"Cannot import {id!r}.")
                return None
            return state
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to import qube", str(exc))
            return None
