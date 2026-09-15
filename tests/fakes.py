# SPDX-License-Identifier: GPL-3.0-or-later
"""In-memory fakes mimicking the slice of qubesadmin the provider uses.

Models the Admin API semantics the provider relies on: property_is_default(),
the qubesadmin.DEFAULT sentinel (Reset), VM-valued props returning objects with
.name, is_halted(), and domains.refresh_cache(). qubesadmin is importable here
(system site-packages) so we reuse its real DEFAULT sentinel.

Also provides the harness (``install_qube_fakes``) that lets the bundled
qubes-ansible ``QubeModule`` / ``qube_facts`` run against these fakes: on this dev
box the installed qubesadmin lacks ``device_protocol``, which leaves
``qubes_module_qube.qubesadmin`` set to ``None`` and ``DeviceAssignment`` /
``AssignmentMode`` undefined — so we patch those module globals back.
"""

import importlib

import qubesadmin
import qubesadmin.exc


class FakePolicyClient:
    """In-memory stand-in for qrexec's ``PolicyClient`` (the slice QubesPolicy uses):
    a name -> file-content store. ``policy_get`` returns ``(content, token)``."""

    def __init__(self, files=None):
        self.files = files if files is not None else {}

    def policy_list(self):
        return sorted(self.files)

    def policy_get(self, name):
        return self.files[name], "sha256:fake"

    def policy_replace(self, name, content, token="any"):
        self.files[name] = content

    def policy_remove(self, name, token="any"):
        self.files.pop(name, None)


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

    def __eq__(self, other):  # like QubesVM: equal to a VM or name of that name
        return self.name == getattr(other, "name", other)

    def __hash__(self):
        return hash(self.name)


class FakeVolume:
    """A qube storage volume, exposing the fields ``qube_facts`` reads."""

    def __init__(self, name, size=2 ** 30, revisions_to_keep=0):
        self.name = name
        self.size = size
        self.revisions_to_keep = revisions_to_keep
        self.pool = "vm-pool"
        self.vid = f"vm-{name}"
        self.rw = True
        self.source = None
        self.save_on_stop = name in ("private", "root")
        self.snap_on_start = name in ("root", "volatile")
        self.usage = 0
        self.ephemeral = False

    def resize(self, size):
        self.size = int(size)


class FakeAssignmentMode:
    def __init__(self, value):
        self.value = value


class FakeVirtualDevice:
    def __init__(self, backend_domain, port_id, device_id):
        self.backend_domain = backend_domain
        self.port_id = port_id
        self.device_id = device_id

    def __repr__(self):
        return f"{self.backend_domain}:{self.port_id}:{self.device_id}"


class FakeDeviceAssignment:
    """Stand-in for qubesadmin.device_protocol.DeviceAssignment."""

    def __init__(self, device, mode=None, options=None, frontend_domain=None):
        self.virtual_device = device
        self.device = device
        self.mode = FakeAssignmentMode(mode) if isinstance(mode, str) else mode
        self.options = options or {}
        self.frontend_domain = frontend_domain


class FakeDeviceCollection:
    def __init__(self, vm, devclass):
        self.vm = vm
        self.devclass = devclass
        self._assigned = []

    def get_assigned_devices(self):
        return list(self._assigned)

    def get_attached_devices(self):
        return []

    def get_exposed_devices(self):
        return []

    def assign(self, assignment):
        self._assigned.append(assignment)

    def unassign(self, assignment):
        target = assignment.virtual_device or assignment.device

        def same(a):
            d = a.virtual_device or a.device
            return (d.backend_domain, d.port_id, d.device_id) == (
                target.backend_domain, target.port_id, target.device_id)

        self._assigned = [a for a in self._assigned if not same(a)]


class FakeDeviceManager(dict):
    def __init__(self, vm):
        super().__init__()
        self.vm = vm

    def __missing__(self, devclass):
        coll = FakeDeviceCollection(self.vm, devclass)
        self[devclass] = coll
        return coll


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
        object.__setattr__(self, "_paused", False)
        object.__setattr__(self, "_notes", "")
        object.__setattr__(self, "shutdown_timeout", 60)
        object.__setattr__(self, "provides_network", False)
        object.__setattr__(self, "writes", [])  # managed-property writes, for idempotency tests
        object.__setattr__(self, "features", {})   # dict-like, like vm.features
        object.__setattr__(self, "tags", set())    # set-like, like vm.tags
        object.__setattr__(self, "devices", FakeDeviceManager(self))
        object.__setattr__(self, "volumes", {
            "root": FakeVolume("root"),
            "private": FakeVolume("private"),
            "volatile": FakeVolume("volatile"),
        })
        self._props["label"] = label  # label is explicit, never default-following
        # netvm/memory/maxmem/template_for_dispvms start default-following.
        for p in ("netvm", "memory", "maxmem", "template_for_dispvms"):
            self._defaults.add(p)
            self._props[p] = self.DEFAULTS[p]
        if klass in self.HAS_TEMPLATE:
            # qubesd pins the template to a is_concrete value at create (not default).
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
            # qubesd refuses to unset an AppVM/DispVM template (Reset); model it so
            # the "*default*" template path can never regress to the Reset path.
            if key == "template" and value is qubesadmin.DEFAULT:
                raise qubesadmin.exc.QubesException(
                    "Cannot unset template. You can set it to the current default instead"
                )
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

    def property_list(self):
        return sorted(self._props)

    def property_is_default(self, name):
        if name == "template" and self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        return name in self._defaults

    def property_get_default(self, name):  # mirrors admin.vm.property.GetDefault
        if name == "template" and self.klass not in self.HAS_TEMPLATE:
            raise AttributeError("template")
        val = self.DEFAULTS[name]
        return _Ref(val) if name in self.REF else val

    def get_notes(self):
        return self._notes

    def set_notes(self, notes):
        object.__setattr__(self, "_notes", notes)

    def is_halted(self):
        return not self._running and not self._paused

    def is_running(self):
        return self._running

    def is_paused(self):
        return self._paused

    def start(self):
        object.__setattr__(self, "_running", True)
        object.__setattr__(self, "_paused", False)

    def pause(self):
        object.__setattr__(self, "_paused", True)

    def unpause(self):
        object.__setattr__(self, "_paused", False)
        object.__setattr__(self, "_running", True)

    def kill(self):
        object.__setattr__(self, "_running", False)
        object.__setattr__(self, "_paused", False)

    def shutdown(self, force=False, wait=False):
        object.__setattr__(self, "_running", False)
        object.__setattr__(self, "_paused", False)

    def get_power_state(self):
        if self._paused:
            return "Paused"
        return "Running" if self._running else "Halted"

    @property
    def derived_vms(self):
        # Mirrors qubesadmin's QubesVM.derived_vms: the qubes based on this one.
        out = []
        for vm in getattr(self, "_domains", []):
            based_on = getattr(vm, "template", None)   # None for Template/Standalone
            if based_on is not None and based_on.name == self.name:
                out.append(vm)
        return out

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

    def get(self, key, default=None):
        return self._d.get(key, default)

    def refresh_cache(self, force=False):
        pass

    def add(self, vm):
        vm._domains = self          # backref so a VM can enumerate its siblings
        self._d[vm.name] = vm


class _FakeAppLabel:
    """A label in ``app.labels`` (has .index and .color, like qubesadmin's Label)."""

    def __init__(self, index, color):
        self.index = index
        self.color = color


class _FakePool:
    """A pool in ``app.pools`` (driver + optional usage/size, like qubesadmin's Pool)."""

    def __init__(self, driver, usage=None, size=None):
        self.driver = driver
        self.usage = usage
        self.size = size


class FakeApp:
    """Stands in for ``qubesadmin.Qubes()`` — domains plus the dom0/system-wide
    catalog (global properties, labels, pools, vm/device classes)."""

    def __init__(self):
        self.domains = FakeDomains()
        self.domains.add(FakeVM("fedora-40", "TemplateVM", "black"))
        netvm = FakeVM("sys-firewall", "AppVM", "green")
        object.__setattr__(netvm, "provides_network", True)
        self.domains.add(netvm)

        # dom0 global properties (admin.property.*). VM-valued ones return a VM
        # object (-> .name); a couple are at their Qubes default.
        self._global_props = {
            "default_template": self.domains["fedora-40"],
            "default_netvm": self.domains["sys-firewall"],
            "default_dispvm": "",
            "clockvm": self.domains["sys-firewall"],
            "default_kernel": "6.18.31",
            "default_qrexec_timeout": 60,
            "check_updates_vm": True,
        }
        self._global_defaults = {"default_qrexec_timeout", "check_updates_vm"}
        # Value each global takes when reset to default (admin.property.Reset).
        self._global_default_values = {
            "default_template": "default-template",
            "default_netvm": "default-netvm",
            "default_dispvm": "",
            "clockvm": "",
            "default_kernel": "default-kernel",
            "default_qrexec_timeout": 60,
            "check_updates_vm": True,
        }

        self.labels = {
            "red": _FakeAppLabel(1, "0xcc0000"),
            "blue": _FakeAppLabel(6, "0x3465a4"),
            "black": _FakeAppLabel(8, "0x000000"),
        }
        self.pools = {
            "vm-pool": _FakePool("lvm_thin", usage=1219867699052, size=1775385968640),
            "varlibqubes": _FakePool("file", usage=16752549888, size=20957446144),
            "linux-kernel": _FakePool("linux-kernel"),   # usage/size None -> omitted
        }
        self.pool_drivers = ["callback", "file", "file-reflink", "linux-kernel",
                             "lvm_thin", "zfs"]

    # --- dom0 global properties (PropertyHolder semantics) ------------------

    def property_list(self):
        return list(self._global_props)

    def property_is_default(self, name):
        return name in self._global_defaults

    def __getattr__(self, name):
        # Global properties are exposed as attributes (like qubesadmin's app).
        try:
            props = object.__getattribute__(self, "_global_props")
        except AttributeError:
            raise AttributeError(name)
        if name in props:
            return props[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        # Writing a global property mirrors admin.property.Set / .Reset; everything
        # else (domains, labels, _internal, ...) is a normal attribute.
        if name.startswith("_") or name not in getattr(self, "_global_props", {}):
            return object.__setattr__(self, name, value)
        if value is qubesadmin.DEFAULT:                  # admin.property.Reset
            self._global_defaults.add(name)
            self._global_props[name] = self._global_default_values[name]
        else:                                            # admin.property.Set
            self._global_defaults.discard(name)
            self._global_props[name] = value

    # --- catalogs -----------------------------------------------------------

    def list_vmclass(self):
        return ["AdminVM", "AppVM", "DispVM", "StandaloneVM", "TemplateVM"]

    def list_deviceclass(self):
        return ["pci", "usb", "block", "mic"]

    # --- VM lifecycle (used by the qube modules) ----------------------------

    def add_new_vm(self, cls, name, label, template=None, pool=None, pools=None):
        vm = FakeVM(name, cls, label, template=template)
        self.domains.add(vm)
        return vm

    def clone_vm(self, src_vm, new_name, new_cls=None, ignore_devices=False,
                 pool=None, pools=None):
        src = src_vm if isinstance(src_vm, FakeVM) else self.domains[src_vm]
        template = src._props.get("template")
        vm = FakeVM(new_name, new_cls or src.klass, str(src.label), template=template)
        # Like qubesadmin's clone_vm: copy non-default properties, features, tags.
        for prop in src.property_list():
            if not src.property_is_default(prop):
                vm._props[prop] = src._props[prop]
                vm._defaults.discard(prop)
        vm.features.update(src.features)
        vm.tags.update(t for t in src.tags if not t.startswith("created-by-"))
        self.domains.add(vm)
        return vm


class FakeHelper:
    """Stand-in for qubes-ansible's ``QubesHelper`` (only what QubeModule uses).

    ``install_qube_fakes`` injects the shared :class:`FakeApp` via ``_app``."""

    _app = None

    def __init__(self, module):
        self.app = FakeHelper._app
        self.module = module

    def get_vm(self, name):
        return self.app.domains[name]

    def create(self, vmname, vmtype=None, label="red", template=None, netvm="*default*"):
        self.app.add_new_vm(vmtype or "AppVM", vmname, label, template=template or None)
        return 0

    def shutdown(self, vmname, wait=False, force=False):
        self.app.domains[vmname].shutdown(force=force)
        return 0

    def remove(self, vmname):
        del self.app.domains[vmname]
        return 0

    def get_device_classes(self):
        return [c for c in self.app.list_deviceclass() if c != "testclass"]

    def parse_device(self, spec):
        devclass, rest = spec.split(":", 1)
        backend, port, devid = rest.split(":", 2)
        return devclass, FakeVirtualDevice(backend, port, devid)

    def list_assigned_devices(self, vmname, devclass):
        vm = self.get_vm(vmname)
        current = {}
        for ass in vm.devices[devclass].get_assigned_devices():
            d = getattr(ass, "virtual_device", None) or ass.device
            spec = f"{devclass}:{d.backend_domain}:{d.port_id}:{d.device_id}"
            current[spec] = (getattr(ass, "mode", None), getattr(ass, "options", None) or {})
        return current

    def assign(self, vmname, devclass, assignment):
        self.get_vm(vmname).devices[devclass].assign(assignment)
        return 0

    def unassign(self, vmname, devclass, assignment):
        self.get_vm(vmname).devices[devclass].unassign(assignment)
        return 0

    def sync_devices(self, vmname, devclass, desired):
        desired_map = {
            f"{devclass}:{vd.backend_domain}:{vd.port_id}:{vd.device_id}": (vd, mode, opts or {})
            for vd, mode, opts in (desired or [])
        }
        current = self.list_assigned_devices(vmname, devclass)
        changed = False
        for spec in set(current) - set(desired_map):
            _, dev = self.parse_device(spec)
            self.unassign(vmname, devclass, FakeDeviceAssignment(dev))
            changed = True
        for spec in set(desired_map) - set(current):
            vd, mode, opts = desired_map[spec]
            resolved = mode or ("required" if devclass == "pci" else "auto-attach")
            self.assign(vmname, devclass, FakeDeviceAssignment(vd, mode=resolved, options=opts))
            changed = True
        return changed


def install_qube_fakes(monkeypatch, app):
    """Wire the bundled qubes-ansible modules to run against ``app`` (a FakeApp).

    Bootstraps the collection, then patches ``qube_module`` globals (QubesHelper,
    the device_protocol-nulled ``qubesadmin``, DeviceAssignment/AssignmentMode)
    and ``qube_facts``' ``qubesadmin.Qubes`` to return ``app``."""
    from qubes_provider.utils import qubes_adapter as adapter

    adapter._bootstrap()
    qube_module = importlib.import_module("qubes_module_qube")
    qube_facts = importlib.import_module("qube_facts")

    FakeHelper._app = app
    monkeypatch.setattr(qube_module, "QubesHelper", FakeHelper)
    monkeypatch.setattr(qube_module, "qubesadmin", qubesadmin)
    monkeypatch.setattr(qube_module, "DeviceAssignment", FakeDeviceAssignment, raising=False)
    monkeypatch.setattr(qube_module, "AssignmentMode", FakeAssignmentMode, raising=False)

    class _QubesShim:
        @staticmethod
        def Qubes():
            return app

    monkeypatch.setattr(qube_facts, "qubesadmin", _QubesShim)

    # The adapter talks to qubesadmin directly (declarative pruning, klass lookups
    # in _with_klass / _prune_undeclared), so point its module-level qubesadmin at
    # the same fake app — mirroring the qube_facts patch above.
    monkeypatch.setattr(adapter, "qubesadmin", _QubesShim)
    return app
