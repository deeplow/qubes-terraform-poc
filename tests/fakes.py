"""In-memory fakes mimicking the slice of qubesadmin the provider uses.

Lets the provider be unit-tested against the real provider logic. ``qubesadmin``
is importable here (system site-packages) so we reuse its real ``DEFAULT`` sentinel.
"""

import qubesadmin

# Default values qubesd would resolve; mirrored by FakeApp.default_* and GetDefault.
DEFAULT_TEMPLATE = "default-template"
DEFAULT_NETVM = "default-netvm"


class FakeLabel:
    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name


class FakeVM:
    # Only these classes have a `template` property; others raise AttributeError.
    HAS_TEMPLATE = {"AppVM", "DispVM"}

    def __init__(self, name, klass, label, template=None):
        self.name = name
        self.klass = klass
        self._label = label
        self.memory = 400
        self.maxmem = 4000
        self.template_for_dispvms = False
        self._running = False
        self._defaults = set()  # ref-props genuinely in the default-following state
        self._props = {}        # resolved value for ref-props (name or None)
        # A freshly created VM's netvm genuinely follows the default netvm.
        self._defaults.add("netvm")
        self._props["netvm"] = DEFAULT_NETVM
        if klass in self.HAS_TEMPLATE:
            # qubesd PINS the template to the default *value* at create (no template
            # given) -> NOT default-following, but value equals the default.
            self._props["template"] = DEFAULT_TEMPLATE if template is None else str(template)

    _DEFAULTS = {"template": DEFAULT_TEMPLATE, "netvm": DEFAULT_NETVM}

    # mirrors qubesadmin: True only if the property is in the default-following state.
    def property_is_default(self, name):
        if name == "template" and self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        return name in self._defaults

    # mirrors admin.vm.property.GetDefault: the resolved default value.
    def property_get_default(self, name):
        if name == "template" and self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        return self._DEFAULTS[name]

    def _set_ref(self, key, value):
        if value is qubesadmin.DEFAULT:   # -> admin.vm.property.Reset
            self._defaults.add(key)
            self._props[key] = self._DEFAULTS[key]
        else:
            self._defaults.discard(key)
            self._props[key] = None if value in (None, "") else str(value)

    # label is a Label object in qubesadmin; str() yields its name.
    @property
    def label(self):
        return FakeLabel(self._label)

    @label.setter
    def label(self, value):
        self._label = str(value)

    @property
    def template(self):
        if self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        return self._props["template"]

    @template.setter
    def template(self, value):
        self._set_ref("template", value)

    @property
    def netvm(self):
        return self._props["netvm"] or None

    @netvm.setter
    def netvm(self, value):
        self._set_ref("netvm", value)

    def is_running(self):
        return self._running

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

    def add(self, vm):
        self._d[vm.name] = vm


class FakeApp:
    """Stands in for ``qubesadmin.Qubes()``."""

    default_template = DEFAULT_TEMPLATE
    default_netvm = DEFAULT_NETVM

    def __init__(self):
        self.domains = FakeDomains()
        # Seed a template and a netvm to reference.
        self.domains.add(FakeVM("fedora-40", "TemplateVM", "black"))
        self.domains.add(FakeVM("sys-firewall", "AppVM", "green"))

    def add_new_vm(self, cls, name, label, template=None, pool=None, pools=None):
        vm = FakeVM(name, cls, label, template=template)
        self.domains.add(vm)
        return vm
