# SPDX-License-Identifier: GPL-3.0-or-later
"""qubesadmin version compatibility shims, kept off the adapter seam.

qubes-ansible targets Qubes 4.3. On 4.2 a few qubesadmin APIs are missing;
:func:`install_qubesadmin_compat` adds each only when absent (so 4.3 is left
untouched). The adapter calls this once during bootstrap, before importing any
qubes-ansible module.
"""

import sys
import types

from .errors import QubesProviderError


def install_qubesadmin_compat() -> None:
    """Bridge the qubesadmin API gaps between Qubes 4.2 and 4.3.

    Three things are missing on 4.2, each shimmed only if absent:

    - ``qubesadmin.device_protocol`` — its absence makes ``qubes_helper`` /
      ``qubes_module_qube`` set ``qubesadmin = None`` (their import try/except),
      which then crashes ``QubesHelper()``. A stub module keeps qubesadmin intact;
      actual device *assignment* still needs 4.3 (the stubs aren't functional).
    - ``QubesVM.get_notes`` / ``set_notes`` — ``qube_facts`` reads notes
      unconditionally; on 4.2 read returns ``""`` and writing notes errors clearly.
    - ``DeviceCollection.get_{assigned,attached,exposed}_devices`` — ``qube_facts``
      reads device state unconditionally; map to 4.2's ``assignments``/``attached``/
      ``available`` (read-only; only needs to be repr-able)."""
    try:
        import qubesadmin  # noqa: PLC0415
    except ImportError:  # pragma: no cover - no qubesadmin off-Qubes; nothing to shim
        return

    try:
        import qubesadmin.device_protocol  # noqa: F401, PLC0415
    except ImportError:
        stub = types.ModuleType("qubesadmin.device_protocol")

        class ProtocolError(Exception):
            pass

        class VirtualDevice:  # pragma: no cover - placeholder, device writes need 4.3
            pass

        class DeviceAssignment:  # pragma: no cover - placeholder
            pass

        class AssignmentMode:  # pragma: no cover - placeholder
            pass

        for _name, _obj in (
            ("ProtocolError", ProtocolError), ("VirtualDevice", VirtualDevice),
            ("DeviceAssignment", DeviceAssignment), ("AssignmentMode", AssignmentMode),
        ):
            setattr(stub, _name, _obj)
        sys.modules["qubesadmin.device_protocol"] = stub
        qubesadmin.device_protocol = stub

    from qubesadmin import vm as _vm  # noqa: PLC0415
    if not hasattr(_vm.QubesVM, "get_notes"):
        def get_notes(self):
            return ""

        def set_notes(self, notes):
            raise QubesProviderError(
                "setting qube notes requires Qubes 4.3+ (qubesadmin has no notes API)"
            )

        _vm.QubesVM.get_notes = get_notes
        _vm.QubesVM.set_notes = set_notes

    from qubesadmin import devices as _devices  # noqa: PLC0415
    if not hasattr(_devices.DeviceCollection, "get_assigned_devices"):
        _devices.DeviceCollection.get_assigned_devices = (
            lambda self: self.assignments()
        )
        _devices.DeviceCollection.get_attached_devices = (
            lambda self: self.attached()
        )
        _devices.DeviceCollection.get_exposed_devices = (
            lambda self: self.available()
        )
