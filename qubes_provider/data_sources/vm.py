"""The ``qubes_vm`` data source: read an existing qube by name."""

from typing import Optional

from tf import schema, types
from tf.iface import ReadDataContext
from tf.provider import DataSource

from ..client import QubesProviderError, get_app, read_vm_state


class QubesVMDataSource(DataSource):
    """Look up a single existing Qubes VM and expose its current properties."""

    def __init__(self, provider):
        self.provider = provider

    @classmethod
    def get_name(cls) -> str:
        return "vm"  # -> data source type "qubes_vm"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(
            attributes=[
                schema.Attribute("name", types.String(), required=True),
                schema.Attribute("vm_class", types.String(), computed=True),
                schema.Attribute("label", types.String(), computed=True),
                schema.Attribute("template", types.String(), computed=True),
                schema.Attribute("memory", types.Number(), computed=True),
                schema.Attribute("maxmem", types.Number(), computed=True),
                schema.Attribute("netvm", types.String(), computed=True),
                schema.Attribute("template_for_dispvms", types.Bool(), computed=True),
                schema.Attribute("provisioned", types.Bool(), computed=True),
                schema.Attribute(
                    "power_state", types.String(), computed=True,
                    description="Current power state, e.g. Running, Halted, Paused.",
                ),
            ],
        )

    def read(self, ctx: ReadDataContext, config: dict) -> Optional[dict]:
        try:
            app = get_app()
            name = config["name"]
            if name not in app.domains:
                ctx.diagnostics.add_error("No such qube", f"{name!r} does not exist.")
                return None
            state = read_vm_state(app, name)
            try:
                state["power_state"] = app.domains[name].get_power_state()
            except Exception:  # noqa: BLE001 - power state is best-effort
                state["power_state"] = None
            return state
        except (QubesProviderError, Exception) as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to read qube", str(exc))
            return None
