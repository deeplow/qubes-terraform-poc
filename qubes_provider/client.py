"""Admin API access, structured after the Qubes Ansible/Salt collections.

The provider intentionally encodes **no** per-property nuance. It passes a generic
name->value bag through ``qubesadmin`` (the same client every Qubes tool uses) and
lets **qubesd** be the source of truth for types, defaults and constraints (e.g.
"VM must be halted to change X") — qubesd's errors surface as Terraform diagnostics.

``"*default*"`` (the qubes-ansible/Salt sentinel) means "use the property's current
default value" (via ``admin.vm.property.GetDefault``); it is a no-op when already at
the default. qubesd refuses to *unset* some properties (e.g. an AppVM ``template``),
so we set the default value rather than Reset.
"""

from typing import Any, Optional

from tf.types import Unknown

DEFAULT_TOKEN = "*default*"
# Handled as dedicated top-level attributes; never enforced via the properties bag.
RESERVED_PROPS = ("name", "vm_class", "label", "template")


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
    """True if ``value`` is a real value (not None and not the tf Unknown sentinel)."""
    return value is not None and value is not Unknown


def as_dict(value) -> dict:
    return dict(value) if concrete(value) else {}


def as_set(value) -> set:
    return set(value) if concrete(value) else set()


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


def shutdown_vm(vm, force: bool = False) -> None:
    """Shut down a qube and wait for it to halt (mirrors qubes_helper.shutdown)."""
    import asyncio  # noqa: PLC0415
    import contextlib  # noqa: PLC0415

    import qubesadmin.events.utils  # noqa: PLC0415
    from qubesadmin.exc import QubesVMNotStartedError  # noqa: PLC0415

    with contextlib.suppress(QubesVMNotStartedError):
        vm.shutdown(force=force)
    if vm.is_halted():
        return
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            asyncio.wait_for(
                qubesadmin.events.utils.wait_for_domain_shutdown([vm]),
                getattr(vm, "shutdown_timeout", 60),
            )
        )
    finally:
        loop.close()


# --- value helpers ----------------------------------------------------------

def _present(vm, attr):
    """(value, present); present=False if the class lacks the property."""
    try:
        return getattr(vm, attr), True
    except (AttributeError, KeyError):
        return None, False


def _str(value) -> str:
    """Canonical string form of a live property value (VM/Label -> name; None -> "")."""
    if hasattr(value, "name"):
        value = value.name
    return "" if value is None else str(value)


def _default_str(app, vm, key: str) -> str:
    """Current default value of a property (GetDefault), as a string; "" if none."""
    try:
        return _str(vm.property_get_default(key))
    except Exception:  # noqa: BLE001 - GetDefault may be unsupported/denied
        return _str(getattr(app, "default_" + key, None))


def shutdown_for_template_update(app, vm, planned: dict) -> None:
    """Halt a running qube before a template change (mirrors qubes-ansible's
    _shutdown_for_template_update). qubesd refuses to change template while running;
    if ``shutdown_if_required`` shut it down first, else raise a clear error."""
    template = planned.get("template")
    if vm.klass == "StandaloneVM" or not concrete(template):
        return
    target = _default_str(app, vm, "template") if template == DEFAULT_TOKEN else template
    current, present = _present(vm, "template")
    if not present or _str(current) == target or vm.is_halted():
        return
    if planned.get("shutdown_if_required"):
        shutdown_vm(vm, force=bool(planned.get("force_shutdown")))
    else:
        raise QubesProviderError(
            (
                "Cannot change the template while the qube is running.\n"
                "If justified, please set 'shutdown_if_required = true'."
            )
        )


# --- read ------------------------------------------------------------------

def read_property(app, vm, key: str, desired: Optional[str]) -> Optional[str]:
    """Read one property as a string. If the config asked for "*default*" and the
    live value is (or equals) the property's current default, report "*default*"."""
    value, present = _present(vm, key)
    if not present:
        return None
    if desired == DEFAULT_TOKEN:
        try:
            is_default = vm.property_is_default(key)
        except (AttributeError, KeyError):
            is_default = False
        if is_default or _str(value) == _default_str(app, vm, key):
            return DEFAULT_TOKEN
    return _str(value)


def _read_feature(vm, key: str) -> Optional[str]:
    try:
        return str(vm.features[key])
    except KeyError:
        return None


def read_vm_state(app, name: str, desired: Optional[dict] = None) -> dict:
    """Map a live Qubes VM onto the ``qubes_vm`` Terraform state shape, reading back
    only the properties/features/tags the config declared (so it round-trips)."""
    desired = desired or {}
    app.domains.refresh_cache(force=True)
    vm = app.domains[name]
    want_props = as_dict(desired.get("properties"))
    want_feats = as_dict(desired.get("features"))
    want_tags = as_set(desired.get("tags"))
    props = {k: read_property(app, vm, k, want_props[k]) for k in want_props}
    feats = {k: _read_feature(vm, k) for k in want_feats}
    return {
        "name": vm.name,
        "vm_class": vm.klass,
        "label": str(vm.label),
        "template": read_property(app, vm, "template", desired.get("template")),
        "properties": {k: v for k, v in props.items() if v is not None},
        "features": {k: v for k, v in feats.items() if v is not None},
        "tags": sorted(t for t in want_tags if t in vm.tags),
    }


# --- enforce (generic; mirrors qvm-prefs / Salt prefs / Ansible) -----------

def enforce_properties(app, vm, props: dict) -> None:
    """Set each property from a generic ``{name: value}`` map (string values).
    ``"*default*"`` sets the current default value; otherwise the value is passed
    through verbatim (qubesadmin coerces, qubesd validates). Writes only on change.
    """
    from qubesadmin import exc as qexc  # noqa: PLC0415

    for name, want in props.items():
        if name in RESERVED_PROPS and name not in ("label", "template"):
            continue
        try:
            if want == DEFAULT_TOKEN:
                target = _default_str(app, vm, name)
                if _str(_present(vm, name)[0]) != target:
                    setattr(vm, name, target or None)
                continue
            try:
                before = DEFAULT_TOKEN if vm.property_is_default(name) else _str(getattr(vm, name))
            except (AttributeError, KeyError):
                before = None
            if before != want:
                setattr(vm, name, want)
        except qexc.QubesNoSuchPropertyError as exc:
            raise QubesProviderError(f"invalid property '{name}'") from exc
        except qexc.QubesValueError as exc:
            raise QubesProviderError(f"invalid value for '{name}': {exc}") from exc
        except qexc.QubesException as exc:
            raise QubesProviderError(f"error setting '{name}': {exc}") from exc


def enforce_features(vm, planned: dict, current: dict) -> None:
    """Apply a generic feature map; remove features dropped from the config."""
    from qubesadmin import exc as qexc  # noqa: PLC0415

    try:
        for key, value in planned.items():
            if _read_feature(vm, key) != str(value):
                vm.features[key] = value
        for key in current:
            if key not in planned and key in vm.features:
                del vm.features[key]
    except qexc.QubesException as exc:
        raise QubesProviderError(f"error setting feature: {exc}") from exc


def enforce_tags(vm, planned: set, current: set) -> None:
    """Ensure the declared tags are present; remove ones dropped from the config
    (only ever touches tags this resource declares — Qubes auto-tags are untouched)."""
    from qubesadmin import exc as qexc  # noqa: PLC0415

    try:
        for tag in planned - current:
            vm.tags.add(tag)
        for tag in current - planned:
            vm.tags.discard(tag)
    except qexc.QubesException as exc:
        raise QubesProviderError(f"error setting tag: {exc}") from exc
