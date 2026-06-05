"""The ``qubes_vm`` managed resource: full CRUD for a Qubes VM."""

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

from ..client import (
    QubesProviderError,
    as_dict,
    as_set,
    concrete,
    create_vm,
    enforce_features,
    enforce_properties,
    enforce_tags,
    get_app,
    get_vm,
    read_vm_state,
    remove_vm,
    shutdown_for_template_update,
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
                        'Template to base this VM on. "*default*" uses the Qubes '
                        "default template; a name uses that template."
                    ),
                ),
                # Generic bags: any qube property/feature/tag, passed through
                # qubesadmin and validated by qubesd. Values are strings;
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
                    optional=True, computed=True,
                    description="Qube features (vm.features); string values.",
                ),
                schema.Attribute(
                    "tags", types.Set(types.String()),
                    optional=True, computed=True,
                    description="Tags this resource manages (Qubes auto-tags untouched).",
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
            ],
        )

    # --- CRUD ---------------------------------------------------------------

    @staticmethod
    def _prop_wants(planned: dict) -> dict:
        """Property bag for enforce: the `properties` map plus the dedicated
        `label`/`template` (which are also qube properties)."""
        wants = dict(as_dict(planned.get("properties")))
        wants["label"] = planned["label"]
        if concrete(planned.get("template")):
            wants["template"] = planned["template"]
        return wants

    def _enforce_all(self, app, vm, planned: dict, current: dict) -> None:
        # Halt-before-template-change first (mirrors Ansible), then properties.
        shutdown_for_template_update(app, vm, planned)
        enforce_properties(app, vm, self._prop_wants(planned))
        enforce_features(vm, as_dict(planned.get("features")), as_dict(current.get("features")))
        enforce_tags(vm, as_set(planned.get("tags")), as_set(current.get("tags")))

    @staticmethod
    def _with_flags(state: Optional[dict], src: dict) -> Optional[dict]:
        """Echo the behavioral flags into state (they are config, not qube state)."""
        if state is not None:
            state["shutdown_if_required"] = src.get("shutdown_if_required")
            state["force_shutdown"] = src.get("force_shutdown")
        return state

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        app = get_app()
        name = planned["name"]
        try:
            vm = create_vm(app, name, planned["vm_class"],
                           planned["label"], planned.get("template"))
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to create qube", f"{name}: {exc}")
            return None
        try:
            self._enforce_all(app, vm, planned, {})
            return self._with_flags(read_vm_state(app, name, desired=planned), planned)
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            # roll back the partially-created qube so a retry isn't blocked by it.
            try:
                remove_vm(app, name)
            except Exception:  # noqa: BLE001
                pass
            ctx.diagnostics.add_error("Failed to create qube", f"{name}: {exc}")
            return None

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
        try:
            app = get_app()
            if current["name"] not in app.domains:
                return None  # drifted / removed out-of-band -> Terraform recreates
            return self._with_flags(
                read_vm_state(app, current["name"], desired=current), current)
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
            self._enforce_all(app, get_vm(app, name), planned, current)
            return self._with_flags(read_vm_state(app, name, desired=planned), planned)
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to update qube", str(exc))
            return None

    def delete(self, ctx: DeleteContext, current: dict):
        try:
            remove_vm(get_app(), current["name"])
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
