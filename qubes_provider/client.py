"""Thin wrapper around the Qubes Admin API (``qubesadmin``).

``qubesadmin.Qubes()`` auto-detects its transport: a local UNIX socket to
``qubesd`` when running in dom0, or qrexec ``admin.vm.*`` calls when running in a
management qube (gated by the Admin API qrexec policy). The rest of the provider
therefore never has to care where it runs.
"""

from typing import Any, Optional

# ``Unknown`` is the sentinel the tf framework uses for not-yet-known (computed)
# values during plan/apply. We must never push it down into Qubes.
from tf.types import Unknown


class QubesProviderError(Exception):
    """Raised for provider-level failures (e.g. qubesadmin unavailable)."""


def get_app():
    """Return a connected ``qubesadmin.Qubes()`` application object.

    Imported lazily so the provider module can be imported (and unit-tested) on
    machines without ``qubesadmin`` installed.
    """
    try:
        import qubesadmin  # noqa: PLC0415  (intentional lazy import)
    except ImportError as exc:  # pragma: no cover - exercised only off-Qubes
        raise QubesProviderError(
            "qubesadmin is not available. Run this provider in dom0 or a Qubes "
            "management qube that has the qubes-core-admin-client package installed."
        ) from exc
    return qubesadmin.Qubes()


def concrete(value: Any) -> bool:
    """True if ``value`` is a real, settable value (not None and not Unknown)."""
    return value is not None and value is not Unknown


# Sentinel value, accepted in config for VM-reference properties, meaning
# "let Qubes use its configured default". qubesd has no literal "@default" wire
# value: on create an empty template means "default", on update the default state
# is reached via admin.vm.property.Reset (assigning qubesadmin.DEFAULT).
DEFAULT_TOKEN = "@default"
VM_REF_PROPS = ("template", "netvm")
BOOL_PROPS = ("template_for_dispvms",)


def _opt(vm, attr) -> Optional[Any]:
    """Read an optional Qubes VM property, tolerating classes that lack it."""
    try:
        return getattr(vm, attr)
    except (AttributeError, KeyError):
        return None


def _get_or_absent(vm, attr):
    """Return (value, present). present=False if the class lacks the property
    (e.g. ``template`` on a StandaloneVM) — distinct from a present-but-None value."""
    try:
        return getattr(vm, attr), True
    except (AttributeError, KeyError):
        return None, False


def _default_value(app, vm, key: str) -> Optional[str]:
    """Resolve a property's default value (admin.vm.property.GetDefault), falling
    back to the global app default. Returns its name, "" for none, or None."""
    try:
        d = vm.property_get_default(key)
        return str(d) if d else ""
    except Exception:  # noqa: BLE001 - GetDefault may be unsupported/denied
        g = getattr(app, "default_" + key, None)
        return str(g) if g else None


def _ref_value(app, vm, key: str, desired: Optional[dict]) -> Optional[str]:
    """Round-trip a VM-reference property (template/netvm) to its config string.

    property absent for the class -> None; explicit none -> ""; otherwise the VM
    name. But if the config asked for "@default" AND the live value equals the
    property's default, report "@default" (so a known config value round-trips).
    Note: we cannot rely on property_is_default — qubesd pins an AppVM's template
    to the default *value* at create, so that flag is False there; comparing
    against GetDefault is the reliable signal.
    """
    value, present = _get_or_absent(vm, key)
    if not present:
        return None
    actual = str(value) if value else ""
    if desired and desired.get(key) == DEFAULT_TOKEN:
        if actual == _default_value(app, vm, key):
            return DEFAULT_TOKEN
    return actual


def read_vm_state(app, name: str, desired: Optional[dict] = None) -> dict:
    """Map a live Qubes VM onto the ``qubes_vm`` Terraform state shape.

    ``desired`` (the planned/prior config) lets ref-props round-trip "@default".
    """
    vm = app.domains[name]
    memory = _opt(vm, "memory")
    maxmem = _opt(vm, "maxmem")
    return {
        "name": vm.name,
        "vm_class": vm.klass,
        "label": str(vm.label),
        "template": _ref_value(app, vm, "template", desired),
        "memory": int(memory) if memory is not None else None,
        "maxmem": int(maxmem) if maxmem is not None else None,
        "netvm": _ref_value(app, vm, "netvm", desired),
        "template_for_dispvms": _opt(vm, "template_for_dispvms"),
        "provisioned": True,
    }


def _set_prop(app, vm, key: str, value: Any) -> None:
    """Write one property, mapping the ``@default`` convention onto Qubes."""
    if key in VM_REF_PROPS and value == DEFAULT_TOKEN:
        import qubesadmin  # noqa: PLC0415  (lazy; sentinel triggers property.Reset)

        setattr(vm, key, qubesadmin.DEFAULT)
    elif key in VM_REF_PROPS:
        # "" / None -> explicit none; otherwise resolve to the referenced VM.
        setattr(vm, key, app.domains[value] if value else None)
    elif key in ("memory", "maxmem"):
        setattr(vm, key, int(value))
    elif key in BOOL_PROPS:
        setattr(vm, key, bool(value))
    else:
        setattr(vm, key, value)


def apply_vm_properties(
    app, vm, planned: dict, current: Optional[dict] = None, skip=()
) -> None:
    """Apply settable ``qubes_vm`` properties from ``planned`` onto a live VM.

    Only concrete (non-Unknown, non-None) values are written. On update (``current``
    given) only changed values are written. ``@default`` is skipped at create time
    because a freshly created VM is already at its default (so no Reset is needed,
    nor the admin.vm.property.Reset permission).
    """

    def changed(key: str) -> bool:
        if key in skip:
            return False
        val = planned.get(key)
        if not concrete(val):
            return False
        if current is None and val == DEFAULT_TOKEN:
            return False  # fresh VM is already at default
        return current is None or val != current.get(key)

    for key in ("label", "template", "memory", "maxmem", "netvm", *BOOL_PROPS):
        if changed(key):
            _set_prop(app, vm, key, planned[key])
