# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adapter between the Terraform provider and the bundled **qubes-ansible**.

The provider does not re-implement Qubes VM lifecycle logic; it drives
qubes-ansible's ``QubeModule`` (write) and ``qube_facts`` (read) in-process. This
module is the single seam where that happens:

- :class:`AnsibleModuleShim` stands in for ``AnsibleModule`` (the only contract the
  qube modules use: ``params`` / ``fail_json`` / ``exit_json``) — no Ansible runtime
  is installed or needed.
- :class:`TerraformAnsibleAdapter` is the reusable base: it runs an Ansible module's
  entrypoint against a params dict and holds the standard transforms from Terraform's
  typed/optional/unknown values into the plain dict/list/scalar params Ansible wants.
- :class:`QubesVmAdapter` targets the ``qube``/``qube_facts`` modules, presenting a
  small Qubes-VM backend (``create_or_update`` / ``delete`` / ``read_vm``) in
  Terraform's vocabulary. The resource talks only to it.

``_bootstrap`` puts the (PEP420-namespace) collection on ``sys.path``, stubs
``ansible.module_utils.basic.AnsibleModule`` so it imports, and installs the
qubesadmin 4.2↔4.3 compat shims.
"""

import os
import sys
import types

from tf.types import Bool, Map, Set, Unknown

from ._compat import install_qubesadmin_compat
from .errors import QubesProviderError

try:
    import qubesadmin  # noqa: PLC0415
except ImportError as exc:  # pragma: no cover
    raise QubesProviderError(
        "qubesadmin is not available. Run this provider in dom0 or a Qubes "
        "management qube that has the qubes-core-admin-client package installed."
    ) from exc

# Root that contains the ``ansible_collections`` namespace package.
_SUBMODULE_ROOT = os.path.join(os.path.dirname(__file__), "helpers", "qubes_ansible")
_BOOTSTRAPPED = False


def _ensure_ansible_stub() -> None:
    """Make ``from ansible.module_utils.basic import AnsibleModule`` resolve.

    If a real Ansible is importable we leave it alone; otherwise we register a
    minimal stub package in ``sys.modules``. The qube modules only use the name
    for a type annotation and in their unused ``main()`` — we supply our own
    module object, so the stub is never instantiated."""
    try:
        import ansible.module_utils.basic  # noqa: F401, PLC0415
        return
    except Exception:  # noqa: BLE001 - any failure means "not usable", so stub it
        pass

    ansible = sys.modules.setdefault("ansible", types.ModuleType("ansible"))
    module_utils = sys.modules.setdefault(
        "ansible.module_utils", types.ModuleType("ansible.module_utils")
    )
    basic = types.ModuleType("ansible.module_utils.basic")

    class AnsibleModule:  # pragma: no cover - stub, never instantiated
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "stub AnsibleModule should not be instantiated; the Terraform "
                "adapter supplies its own module object"
            )

    basic.AnsibleModule = AnsibleModule
    ansible.module_utils = module_utils
    module_utils.basic = basic
    sys.modules["ansible.module_utils.basic"] = basic


def _bootstrap() -> None:
    """Idempotently make the bundled collection importable."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    if _SUBMODULE_ROOT not in sys.path:
        sys.path.insert(0, _SUBMODULE_ROOT)
    _ensure_ansible_stub()
    install_qubesadmin_compat()  # must precede any qubes-ansible import
    _BOOTSTRAPPED = True


class AnsibleModuleShim:
    """Stand-in for ``AnsibleModule`` exposing only what the qube modules use.

    ``fail_json`` raises (so a failure unwinds into a Terraform diagnostic) and
    ``exit_json`` captures the terminal result into :attr:`result`."""

    def __init__(self, params: dict):
        self.params = params
        self.result = None

    def fail_json(self, msg=None, **kwargs):
        raise QubesProviderError(msg or kwargs.get("msg") or str(kwargs))

    def exit_json(self, **kwargs):
        self.result = kwargs

    def warn(self, *args, **kwargs):  # pragma: no cover - defensive no-op
        pass

    def debug(self, *args, **kwargs):  # pragma: no cover - defensive no-op
        pass


class TerraformAnsibleAdapter:
    """Base Adapter bridging Terraform's declarative model to an Ansible module.

    Owns what every qubes-ansible adapter shares: running a module's entrypoint
    against a params dict via :class:`AnsibleModuleShim`, and the standard
    transforms from Terraform's typed/optional/unknown values into the plain
    dict/list/scalar params Ansible expects. Subclasses target one module."""

    # Sentinel meaning "use the provider's default" (qubes-ansible/Salt's "*default*").
    DEFAULT_TOKEN = "*default*"

    # --- adaptee driver -----------------------------------------------------

    def _run(self, runner, params: dict) -> dict:
        """Bootstrap, build an :class:`AnsibleModuleShim` for ``params``, call
        ``runner(module)`` (e.g. ``qube_facts.core`` or a ``QubeModule(m).run()``
        thunk), and return its captured ``exit_json``. A ``fail_json`` inside the
        module surfaces as :class:`~qubes_provider.errors.QubesProviderError`."""
        _bootstrap()
        module = AnsibleModuleShim(params)
        runner(module)
        return module.result

    # --- declarative -> Ansible value transforms ----------------------------

    @staticmethod
    def is_concrete(value) -> bool:
        """True if ``value`` is a real value (not None and not the tf Unknown sentinel)."""
        return value is not None and value is not Unknown

    @classmethod
    def as_dict(cls, value) -> dict:
        return dict(value) if cls.is_concrete(value) else {}

    @classmethod
    def as_set(cls, value) -> set:
        return set(value) if cls.is_concrete(value) else set()

    # --- declarative convergence primitive ----------------------------------

    @classmethod
    def _key_set(cls, value) -> set:
        """Keys of a Map / elements of a Set, tolerant of None/Unknown."""
        if cls.is_concrete(value) and isinstance(value, dict):
            return set(value)
        return cls.as_set(value)

    @classmethod
    def removed_items(cls, prior: dict, planned: dict, key: str) -> set:
        """Items declared under ``key`` in ``prior`` but dropped from ``planned`` —
        the removals Terraform must apply because Ansible modules only add/set."""
        return cls._key_set(prior.get(key)) - cls._key_set(planned.get(key))

    # --- declarative state -> Ansible module params -------------------------

    def _coerce(self, tf_type, value):
        """Coerce one (already python-tf-decoded) value into an Ansible param value,
        dispatching on its ``TfType``. A Terraform ``null``/unknown maps to a
        type-appropriate default — Terraform treats ``null`` as "omitted, use the
        default", which is exactly Ansible's ``argument_spec`` default semantics.

        ``Set`` decodes to a Python list (and is unordered) so it is canonicalized to
        a sorted, de-duplicated list; ``Map`` to a dict; ``Bool`` to a bool; and any
        scalar / ``NormalizedJson`` (``raw``) value passes through as-is."""
        if isinstance(tf_type, Set):
            return sorted(self.as_set(value))
        if isinstance(tf_type, Map):
            return self.as_dict(value)
        if isinstance(tf_type, Bool):
            return bool(value) if self.is_concrete(value) else False
        return value if self.is_concrete(value) else None

    def to_params(self, desired: dict, types_by_name: dict, *, renames=(), skip=()) -> dict:
        """Convert a Terraform state dict into Ansible module params, driven by the
        schema: ``types_by_name`` maps each Terraform attribute to its ``TfType``.
        Every non-skipped attribute becomes a param, keyed by its Ansible name
        (``renames`` maps tf_name -> ansible_name) and coerced by :meth:`_coerce`.
        Generic only — module-specific rules (constants, folded attrs, sentinels)
        stay in the subclass."""
        renames = dict(renames)
        skip = set(skip)
        params = {}
        for name, tf_type in types_by_name.items():
            if name in skip:
                continue
            params[renames.get(name, name)] = self._coerce(tf_type, desired.get(name))
        return params


class QubesVmAdapter(TerraformAnsibleAdapter):
    """Adapts qubes-ansible's ``QubeModule`` / ``qube_facts`` to a Qubes-VM backend.

    The resource and data source call only the Target verbs; everything below them
    (parameter translation, fact projection, declarative pruning, and the
    AnsibleModule plumbing inherited from the base) stays behind this class.
    """

    # Ansible params whose name differs from the Terraform attribute feeding them.
    _PARAM_RENAMES = {"force_shutdown": "force"}
    # Terraform attributes the generic conversion skips (handled explicitly below).
    _SKIP_ATTRS = {"label"}

    # --- Target: what the resource / data source call -----------------------

    def create_or_update(self, desired: dict, prior: dict | None = None) -> None:
        """Make the qube match ``desired``: create/clone + enforce properties,
        volumes, devices, features, tags and notes via ``QubeModule``; then, when
        ``prior`` is given (update), prune the tags/features/services dropped since
        ``prior`` (qubes-ansible only adds/sets — Terraform converges removals)."""
        self._run_module(self.desired_to_qube_params(desired))
        if prior is not None:
            self._prune_undeclared(desired, prior)

    def delete(self, name: str) -> None:
        self._run_module({"name": name, "state": "absent"})

    def try_delete(self, name: str) -> None:
        """Best-effort delete for create rollback — never raises."""
        try:
            self.delete(name)
        except Exception:  # noqa: BLE001
            pass

    def read_vm(self, name: str, desired: dict) -> dict | None:
        """Full ``qubes_vm`` resource state, projected onto the declared subset,
        or ``None`` if the qube does not exist. ``volumes``/``services``/``notes``
        are read-projected; ``clone_src``/``devices`` and the behavioral flags are
        echoed from ``desired`` (config-only, not reconstructable from a qube's facts)."""
        facts = self._fetch_facts(name)
        if facts is None:
            return None
        desired = self._with_klass(name, desired)
        state = self.qube_facts_to_state(facts, desired)
        state["notes"] = facts.get("notes")
        state["services"] = self._qube_services_to_state(facts, desired)
        state["volumes"] = self._qube_volumes_to_state(facts, desired)
        state["devices"] = desired.get("devices")

        # Behavioral flags: config-only (drive write-time behavior, no qube-fact
        # representation) — echoed from desired
        state["shutdown_if_required"] = desired.get("shutdown_if_required")
        state["force_shutdown"] = desired.get("force_shutdown")
        state["clone_src"] = desired.get("clone_src")

        return state

    # --- Terraform state -> qube-module params ------------------------------

    def desired_to_qube_params(self, desired: dict) -> dict:
        """Translate a ``qubes_vm`` Terraform state dict to ``QubeModule`` params.

        The generic conversion (coercing each attribute by the ``TfType`` declared in
        ``QubesVMResource``'s schema, with the ``force_shutdown``->``force`` rename) is
        done by :meth:`~TerraformAnsibleAdapter.to_params`. Only the qube-specific
        deltas remain here: pin ``state="present"`` (the one required param), fold
        ``label`` into ``properties`` (qubes-ansible has no ``label`` param — it is an
        ordinary qube property), and resolve the ``"*default*"`` template sentinel."""
        from .resources.vm import QubesVMResource  # deferred: avoid an import cycle

        types_by_name = {a.name: a.type for a in QubesVMResource.get_schema().attributes}
        params = self.to_params(
            desired, types_by_name, renames=self._PARAM_RENAMES, skip=self._SKIP_ATTRS
        )

        # Implicit parameters
        params["state"] = "present"

        # Parameters that need wrapping
        # NOTE: label is a top level in the ansible module, but not the library
        if self.is_concrete(desired.get("label")):
            params["properties"] = {**self.as_dict(params.get("properties")),
                                    "label": desired["label"]}

        # "*default*" template = use the qubesd default: drop to None so QubeModule
        # neither passes it to add_new_vm nor Resets the (un-resettable) AppVM template.
        if params["template"] == self.DEFAULT_TOKEN:
            params["template"] = None

        return params

    # --- qube_facts -> Terraform state (projected onto the declared subset) --

    @staticmethod
    def _norm_prop(value) -> str:
        """Canonicalize a stringified property value; None / "None" -> "" (no value)."""
        return "" if value in (None, "None") else str(value)

    def _project_property(self, facts: dict, key: str, desired_value):
        """One property's value for state: ``"*default*"`` when the config asked for
        it and the live value is the property's default; else the canonical value."""
        if desired_value == self.DEFAULT_TOKEN and facts.get("default_properties", {}).get(key):
            return self.DEFAULT_TOKEN
        return self._norm_prop(facts.get("properties", {}).get(key))

    def qube_facts_to_state(self, facts: dict, desired: dict) -> dict:
        """Map a ``qube_facts`` dump onto the ``qubes_vm`` state shape, reporting only
        the properties/features/tags the config declared (so state round-trips without
        spurious diffs). ``klass`` comes from ``desired``.

        ``features``/``tags`` are ``optional`` (not computed): an undeclared bag is
        reported as ``None`` (so deleting the block converges to removal), while a
        declared bag — even an empty one — is projected onto its keys."""
        props = facts.get("properties", {})
        want_props = self.as_dict(desired.get("properties"))
        want_feats = self.as_dict(desired.get("features"))
        want_tags = self.as_set(desired.get("tags"))
        live_feats = facts.get("features", {})
        live_tags = set(facts.get("tags", []))

        # Template is special: after create it is pinned (never default-following), so
        # default detection can't see "*default*". Echo "*default*" when the config
        # asked for it; otherwise report the live (is_concrete) template.
        template = None
        if "template" in props:
            template = (
                self.DEFAULT_TOKEN if desired.get("template") == self.DEFAULT_TOKEN
                else self._norm_prop(props.get("template"))
            )

        return {
            "name": facts["name"],
            "klass": desired.get("klass"),
            "label": props.get("label"),
            "template": template,
            "properties": {k: self._project_property(facts, k, want_props[k]) for k in want_props},
            "features": ({k: live_feats[k] for k in want_feats if k in live_feats}
                         if self.is_concrete(desired.get("features")) else None),
            "tags": (sorted(t for t in want_tags if t in live_tags)
                     if self.is_concrete(desired.get("tags")) else None),
        }

    def _qube_volumes_to_state(self, facts: dict, desired: dict) -> dict:
        """Project declared volumes back into state. ``size`` is grow-only: a declared
        size is echoed when the live volume is at least that big (allocation rounding
        doesn't cause a perpetual diff); otherwise the live byte size is reported,
        forcing a resize on next apply. Other attrs (e.g. ``revisions_to_keep``) are
        echoed from config (pass-through)."""
        want = self.as_dict(desired.get("volumes"))
        by_name = {v["name"]: v for v in facts.get("volumes", [])}
        out = {}
        for name, config in want.items():
            live = by_name.get(name, {})
            reported = {}
            for attr, desired_value in self.as_dict(config).items():
                if attr == "size":
                    actual = int(live.get("size", 0))
                    reported["size"] = (
                        str(desired_value) if int(desired_value) <= actual
                        else str(actual)
                    )
                else:
                    reported[attr] = str(desired_value)
            out[name] = reported
        return out

    def _qube_services_to_state(self, facts: dict, desired: dict) -> list | None:
        """Declared services that are currently enabled. ``qube_facts`` reports
        services top-level as ``{name: bool}`` (True = enabled, False =
        exists-but-disabled); a disabled or unset service does not exist as far as
        Terraform is concerned. ``services`` is ``optional`` (not computed): an
        undeclared block is reported as ``None`` so deleting it converges to removal."""
        if not self.is_concrete(desired.get("services")):
            return None
        live = facts.get("services", {})
        return sorted(s for s in self.as_set(desired.get("services")) if live.get(s))

    # --- declarative pruning (Terraform removes; qubes-ansible only adds) ----

    def _prune_undeclared(self, desired: dict, prior: dict) -> None:
        """Remove tags/features/services present in ``prior`` but dropped from
        ``desired``. Only ever touches declared keys — Qubes auto-tags
        (``created-by-*``) are left alone."""
        vm = qubesadmin.Qubes().domains[desired["name"]]

        for tag in self.removed_items(prior, desired, "tags"):
            vm.tags.discard(tag)

        for feature in self.removed_items(prior, desired, "features"):
            if feature in vm.features:
                del vm.features[feature]

        for service in self.removed_items(prior, desired, "services"):
            feature = f"service.{service}"
            if feature in vm.features:
                del vm.features[feature]

    # --- adaptee drivers & helpers ------------------------------------------

    def _with_klass(self, name: str, desired: dict) -> dict:
        """Ensure ``desired`` carries ``klass`` (import / data source omit it —
        it is not a qubesd property, so read it live from the domain)."""
        if self.is_concrete(desired.get("klass")):
            return desired

        return {**desired, "klass": qubesadmin.Qubes().domains[name].klass}

    def _run_module(self, params: dict) -> dict:
        """Run qubes-ansible's ``QubeModule`` (create/clone + enforce)."""
        def runner(module):
            from ansible_collections.qubesos.core.plugins.module_utils.qubes_module_qube import (  # noqa: E501, PLC0415
                QubeModule,
            )
            QubeModule(module).run()

        return self._run(runner, params)

    def _fetch_facts(self, name: str):
        """Read a qube's facts via qubes-ansible's ``qube_facts``; ``None`` if absent."""
        def runner(module):
            from ansible_collections.qubesos.core.plugins.modules import (  # noqa: PLC0415
                qube_facts,
            )
            qube_facts.core(module)

        try:
            result = self._run(runner, {"name": name})
        except QubesProviderError:
            return None  # qube_facts.core fail_json's only when the qube is absent
        return result["ansible_facts"]["qubes_facts"]
