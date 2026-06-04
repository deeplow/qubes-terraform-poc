"""Admin API access, structured after the Qubes Ansible collection.

Mirrors ``qubes-ansible``'s ``qubes_helper`` + ``enforce_properties``: a generic
property-enforce loop, ``refresh_cache`` before reads, typed error mapping, and a
shutdown-before-template-change step.

Note on ``"*default*"``: qubes-ansible expresses "default" by assigning
``qubesadmin.DEFAULT`` (admin.vm.property.Reset). qubesd refuses that for some
properties — notably an AppVM ``template`` ("Cannot unset template. You can set it
to the current default ... instead"). So here ``"*default*"`` means "set the
property to its current default value" (via admin.vm.property.GetDefault), which is
a no-op when the property is already at its default. This avoids the Reset and
works uniformly for ``template`` and ``netvm``.
"""

from typing import Any, Optional

from tf.types import Unknown

# Sentinel string (identical to qubes-ansible) meaning "use the Qubes default".
DEFAULT_TOKEN = "*default*"

SETTABLE_PROPS = (
    "label", "template", "netvm", "memory", "maxmem", "template_for_dispvms",
)
VM_REF_PROPS = ("template", "netvm")   # value is a VM (or None); supports "*default*"
INT_PROPS = ("memory", "maxmem")
HALT_REQUIRED_PROPS = ("template",)    # require the qube halted before changing


class QubesProviderError(Exception):
    """Raised for provider-level failures (surfaced as Terraform diagnostics)."""


def get_app():
    """Return a connected ``qubesadmin.Qubes()`` (lazy import for off-Qubes use)."""
    try:
        import qubesadmin  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise QubesProviderError(
            "qubesadmin is not available. Run this provider in dom0 or a Qubes "
            "management qube that has the qubes-core-admin-client package installed."
        ) from exc
    return qubesadmin.Qubes()


def concrete(value: Any) -> bool:
    """True if ``value`` is a real, settable value (not None and not Unknown)."""
    return value is not None and value is not Unknown


# --- helpers mirroring qubes_helper.py -------------------------------------

def get_vm(app, name):
    """Refresh the domains cache and return the VM (mirrors qubes_helper.get_vm)."""
    app.domains.refresh_cache(force=True)
    return app.domains[name]


def create_vm(app, name: str, klass: Optional[str], label: str, template):
    """Create a qube (mirrors qubes_helper.create): None/""/"*default*" template
    means "use the default template"; a name is passed through."""
    template_vm = None if template in (None, "", DEFAULT_TOKEN) else template
    return app.add_new_vm(klass or "AppVM", name, label, template=template_vm)


def remove_vm(app, name: str) -> None:
    """Kill the qube if running, then remove it (mirrors qubes_helper.remove)."""
    if name not in app.domains:
        return
    vm = app.domains[name]
    try:
        if not vm.is_halted():
            vm.kill()
    except Exception:  # noqa: BLE001 - best effort before removal
        pass
    del app.domains[name]


def shutdown_vm(vm) -> None:
    """Shut down a qube and wait for it to halt (mirrors qubes_helper.shutdown)."""
    import asyncio  # noqa: PLC0415
    import contextlib  # noqa: PLC0415

    import qubesadmin.events.utils  # noqa: PLC0415
    from qubesadmin.exc import QubesVMNotStartedError  # noqa: PLC0415

    with contextlib.suppress(QubesVMNotStartedError):
        vm.shutdown(force=True)
    if vm.is_halted():
        return
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            asyncio.wait_for(
                qubesadmin.events.utils.wait_for_domain_shutdown([vm]),
                vm.shutdown_timeout,
            )
        )
    finally:
        loop.close()


# --- value helpers ----------------------------------------------------------

def _opt(vm, attr) -> Optional[Any]:
    try:
        return getattr(vm, attr)
    except (AttributeError, KeyError):
        return None


def _get_or_absent(vm, attr):
    """(value, present); present=False if the class lacks the property."""
    try:
        return getattr(vm, attr), True
    except (AttributeError, KeyError):
        return None, False


def _norm(value):
    """Normalize a live property value for comparison (VM/Label -> name; None -> "")."""
    if hasattr(value, "name"):
        return value.name
    if value is None:
        return ""
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def default_value(app, vm, key: str) -> str:
    """Current default value of a property (admin.vm.property.GetDefault), falling
    back to the global ``app.default_<key>``. Returns its name or ""."""
    try:
        return _norm(vm.property_get_default(key))
    except Exception:  # noqa: BLE001 - GetDefault may be unsupported/denied
        return _norm(getattr(app, "default_" + key, None))


# --- read ------------------------------------------------------------------

def _read_ref(app, vm, key: str, desired: Optional[dict]) -> Optional[str]:
    """Round-trip a VM-reference property: class lacks it -> None; no value -> "";
    otherwise the VM name. If the config asked for "*default*" and the live value
    equals the property's current default, report "*default*"."""
    value, present = _get_or_absent(vm, key)
    if not present:
        return None
    name = _norm(value)
    if desired and desired.get(key) == DEFAULT_TOKEN and name == default_value(app, vm, key):
        return DEFAULT_TOKEN
    return name


def read_vm_state(app, name: str, desired: Optional[dict] = None) -> dict:
    """Map a live Qubes VM onto the ``qubes_vm`` Terraform state shape.

    ``desired`` (planned/prior config) lets ref-props round-trip ``"*default*"``.
    """
    app.domains.refresh_cache(force=True)
    vm = app.domains[name]
    memory = _opt(vm, "memory")
    maxmem = _opt(vm, "maxmem")
    return {
        "name": vm.name,
        "vm_class": vm.klass,
        "label": str(vm.label),
        "template": _read_ref(app, vm, "template", desired),
        "memory": int(memory) if memory is not None else None,
        "maxmem": int(maxmem) if maxmem is not None else None,
        "netvm": _read_ref(app, vm, "netvm", desired),
        "template_for_dispvms": _opt(vm, "template_for_dispvms"),
        "provisioned": True,
    }


# --- enforce (mirrors qubes_module_qube.enforce_properties) ----------------

def wants_from_planned(planned: dict) -> dict:
    """Concrete settable {name: value} from a planned config (drop Unknown/None)."""
    return {k: planned[k] for k in SETTABLE_PROPS if concrete(planned.get(k))}


def _shutdown_for_property_update(app, vm, wants: dict) -> None:
    """Halt the qube before changing a property that requires it (mirrors
    _shutdown_for_template_update). Only ``template`` qualifies for now."""
    if vm.klass == "StandaloneVM" or "template" not in wants:
        return
    want = wants["template"]
    current, present = _get_or_absent(vm, "template")
    if not present:
        return
    target = default_value(app, vm, "template") if want == DEFAULT_TOKEN else want
    if _norm(current) != target and not vm.is_halted():
        shutdown_vm(vm)


def enforce_properties(app, vm, wants: dict) -> None:
    """Set each wanted property using the qubes-ansible pattern, but expressing
    "*default*" as the current default value (qubesd forbids unsetting some props).
    Writes only when the value changes; typed Admin API errors are mapped."""
    from qubesadmin import exc as qexc  # noqa: PLC0415

    _shutdown_for_property_update(app, vm, wants)

    for name, want in wants.items():
        try:
            if want == DEFAULT_TOKEN:
                if name not in VM_REF_PROPS:
                    continue  # "*default*" only meaningful for VM-reference props
                target = default_value(app, vm, name)
                if _norm(_opt(vm, name)) != target:
                    setattr(vm, name, target or None)
                continue

            if vm.property_is_default(name):
                before = DEFAULT_TOKEN
            else:
                before = _norm(getattr(vm, name))
            value_to_set = int(want) if name in INT_PROPS else want
            if before != value_to_set:
                setattr(vm, name, value_to_set)
        except qexc.QubesNoSuchPropertyError as exc:
            raise QubesProviderError(f"invalid property '{name}'") from exc
        except qexc.QubesValueError as exc:
            raise QubesProviderError(f"invalid value for '{name}': {exc}") from exc
        except qexc.QubesException as exc:
            raise QubesProviderError(f"error setting '{name}': {exc}") from exc
