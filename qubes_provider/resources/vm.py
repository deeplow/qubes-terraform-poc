"""The ``qubes_vm`` managed resource: full CRUD for a Qubes VM."""

from typing import Optional

from tf import schema, types
from tf.iface import (
    CreateContext,
    DeleteContext,
    ImportContext,
    ReadContext,
    UpdateContext,
    UpgradeContext,
)
from tf.provider import Resource

from ..client import (
    DEFAULT_TOKEN,
    QubesProviderError,
    apply_vm_properties,
    get_app,
    read_vm_state,
)


class QubesVMResource(Resource):
    """Maps a Terraform resource onto a Qubes domain via the Admin API.

    CRUD -> qubesadmin -> Admin API:
      create -> app.add_new_vm(...)            -> admin.vm.Create.<class>
      read   -> app.domains[name] / props      -> admin.vm.property.Get
      update -> vm.<prop> = value              -> admin.vm.property.Set
      delete -> del app.domains[name]          -> admin.vm.Remove
    """

    def __init__(self, provider):
        self.provider = provider

    @classmethod
    def get_name(cls) -> str:
        # Combined with the provider model prefix "qubes_" -> "qubes_vm".
        return "vm"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(
            version=2,
            attributes=[
                # Identity. A Qubes VM name is unique and immutable as an
                # identity, so changing it replaces the resource.
                schema.Attribute(
                    "name", types.String(), required=True, requires_replace=True,
                    description="Unique Qubes VM name.",
                ),
                # VM class cannot be changed in place.
                schema.Attribute(
                    "vm_class", types.String(), required=True, requires_replace=True,
                    description="Qubes VM class: AppVM, TemplateVM, StandaloneVM, DispVM.",
                ),
                schema.Attribute(
                    "label", types.String(), required=True,
                    description="Label color, e.g. red, blue, green.",
                ),
                schema.Attribute(
                    "template", types.String(), optional=True, computed=True,
                    description=(
                        'Template to base this VM on. "@default" uses the Qubes '
                        "default template; a name uses that template."
                    ),
                ),
                schema.Attribute(
                    "memory", types.Number(), optional=True, computed=True,
                    description="Initial memory (MB).",
                ),
                schema.Attribute(
                    "maxmem", types.Number(), optional=True, computed=True,
                    description="Maximum memory for ballooning (MB).",
                ),
                schema.Attribute(
                    "netvm", types.String(), optional=True, computed=True,
                    description=(
                        'NetVM providing network. "@default" uses the Qubes default '
                        'netvm; "" means no network; a name uses that netvm.'
                    ),
                ),
                schema.Attribute(
                    "template_for_dispvms", types.Bool(), optional=True, computed=True,
                    description="Whether this VM may serve as a template for DispVMs.",
                ),
                schema.Attribute(
                    "provisioned", types.Bool(), computed=True,
                    description="True once the VM has been created by this provider.",
                ),
            ],
        )

    # --- CRUD ---------------------------------------------------------------

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        try:
            app = get_app()
            # add_new_vm resolves the template arg itself: None/""/"@default" all
            # mean "use the default template"; a real name is passed through.
            tmpl = planned.get("template")
            create_tmpl = None if tmpl in (None, "", DEFAULT_TOKEN) else tmpl
            vm = app.add_new_vm(
                planned["vm_class"],
                planned["name"],
                planned["label"],
                template=create_tmpl,
            )
            # template already handled by add_new_vm; apply the rest.
            apply_vm_properties(app, vm, planned, skip={"template"})
            return read_vm_state(app, planned["name"], desired=planned)
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            ctx.diagnostics.add_error(
                "Failed to create qube", f"{planned.get('name')}: {exc}"
            )
            return None

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
        try:
            app = get_app()
            if current["name"] not in app.domains:
                return None  # drifted / removed out-of-band -> Terraform recreates
            return read_vm_state(app, current["name"], desired=current)
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            # e.g. qubesadmin.exc.QubesDaemonAccessError when qrexec policy denies access.
            ctx.diagnostics.add_error("Failed to read qube", str(exc))
            return None

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> Optional[dict]:
        try:
            app = get_app()
            name = current["name"]
            if name not in app.domains:
                ctx.diagnostics.add_error(
                    "Qube disappeared", f"{name} no longer exists; cannot update."
                )
                return None
            apply_vm_properties(app, app.domains[name], planned, current)
            return read_vm_state(app, name, desired=planned)
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to update qube", str(exc))
            return None

    def delete(self, ctx: DeleteContext, current: dict):
        try:
            app = get_app()
            name = current["name"]
            if name not in app.domains:
                return
            vm = app.domains[name]
            try:
                if vm.is_running():
                    vm.kill()
            except Exception:  # noqa: BLE001 - best effort before removal
                pass
            del app.domains[name]
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to delete qube", str(exc))

    def import_(self, ctx: ImportContext, id: str) -> Optional[dict]:
        """Adopt an existing qube into Terraform state by name."""
        try:
            app = get_app()
            if id not in app.domains:
                ctx.diagnostics.add_error("No such qube", f"Cannot import {id!r}.")
                return None
            return read_vm_state(app, id)
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to import qube", str(exc))
            return None

    def upgrade(self, ctx: UpgradeContext, version: int, old: dict) -> Optional[dict]:
        # v1 -> v2 added template_for_dispvms; pad prior state with a null.
        old.setdefault("template_for_dispvms", None)
        return old
