"""In-memory fakes mimicking the slice of qubesadmin the provider uses.

Models the Admin API semantics the provider relies on: property_is_default(),
the qubesadmin.DEFAULT sentinel (Reset), VM-valued props returning objects with
.name, is_halted(), and domains.refresh_cache(). qubesadmin is importable here
(system site-packages) so we reuse its real DEFAULT sentinel.
"""

import qubesadmin


class FakeLabel:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name


class _Ref:
    """A VM-valued property result (has .name and str(), like a QubesVM)."""

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name


class FakeVM:
    HAS_TEMPLATE = {"AppVM", "DispVM"}
    MANAGED = ("template", "netvm", "memory", "maxmem", "template_for_dispvms", "label")
    REF = ("template", "netvm")
    # Values qubesd would resolve for a default-following property.
    DEFAULTS = {
        "template": "default-template", "netvm": "default-netvm",
        "memory": 400, "maxmem": 4000, "template_for_dispvms": False,
    }

    def __init__(self, name, klass, label, template=None):
        object.__setattr__(self, "_props", {})
        object.__setattr__(self, "_defaults", set())
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "klass", klass)
        object.__setattr__(self, "_running", False)
        object.__setattr__(self, "shutdown_timeout", 60)
        object.__setattr__(self, "writes", [])  # managed-property writes, for idempotency tests
        object.__setattr__(self, "features", {})   # dict-like, like vm.features
        object.__setattr__(self, "tags", set())    # set-like, like vm.tags
        self._props["label"] = label  # label is explicit, never default-following
        # netvm/memory/maxmem/template_for_dispvms start default-following.
        for p in ("netvm", "memory", "maxmem", "template_for_dispvms"):
            self._defaults.add(p)
            self._props[p] = self.DEFAULTS[p]
        if klass in self.HAS_TEMPLATE:
            # qubesd pins the template to a concrete value at create (not default).
            self._props["template"] = self.DEFAULTS["template"] if template is None else str(template)

    def __getattr__(self, key):
        props = object.__getattribute__(self, "_props")
        if key == "template" and self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        if key in props:
            val = props[key]
            if key in self.REF:
                return _Ref(val) if val else None
            if key == "label":
                return FakeLabel(val)
            return val
        raise AttributeError(key)

    def __setattr__(self, key, value):
        if key in self.MANAGED:
            if key == "template" and self.klass not in self.HAS_TEMPLATE:
                raise AttributeError("template")
            self.writes.append(key)
            if value is qubesadmin.DEFAULT:           # admin.vm.property.Reset
                self._defaults.add(key)
                self._props[key] = self.DEFAULTS.get(key)
            else:
                self._defaults.discard(key)
                if key in self.REF:
                    self._props[key] = None if value in (None, "") else (
                        value.name if hasattr(value, "name") else str(value)
                    )
                else:
                    self._props[key] = value
        else:
            object.__setattr__(self, key, value)

    def property_is_default(self, name):
        if name == "template" and self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        return name in self._defaults

    def property_get_default(self, name):  # mirrors admin.vm.property.GetDefault
        if name == "template" and self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        val = self.DEFAULTS[name]
        return _Ref(val) if name in self.REF else val

    def is_halted(self):
        return not self._running

    def is_running(self):
        return self._running

    def kill(self):
        object.__setattr__(self, "_running", False)

    def shutdown(self, force=False):
        object.__setattr__(self, "_running", False)

    def get_power_state(self):
        return "Running" if self._running else "Halted"

    def __str__(self):
        return self.name


class FakeDomains:
    def __init__(self):
        self._d = {}

    def __contains__(self, key):
        return key in self._d

    def __getitem__(self, key):
        return self._d[key]

    def __delitem__(self, key):
        del self._d[key]

    def __iter__(self):
        return iter(self._d.values())

    def refresh_cache(self, force=False):
        pass

    def add(self, vm):
        self._d[vm.name] = vm


class FakeApp:
    """Stands in for ``qubesadmin.Qubes()``."""

    def __init__(self):
        self.domains = FakeDomains()
        self.domains.add(FakeVM("fedora-40", "TemplateVM", "black"))
        self.domains.add(FakeVM("sys-firewall", "AppVM", "green"))

    def add_new_vm(self, cls, name, label, template=None, pool=None, pools=None):
        vm = FakeVM(name, cls, label, template=template)
        self.domains.add(vm)
        return vm
