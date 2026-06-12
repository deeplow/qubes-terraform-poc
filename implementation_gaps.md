 Plan: implement gaps #3 (volume resize), #4 (global prefs), #8 (device assignment)

 Context

 From the SDW gap analysis (securedrop_salt vs. this provider), three capabilities the
 provider lacks but SDW-class dom0 provisioning needs: resizing a qube's private volume
 (qvm-volume resize sd-app:private), setting dom0 global preferences (qubes-prefs default_template/default_dispvm), and attaching devices (general Qubes capability).

 Design principle unchanged: encode no per-key nuance; pass generic bags through qubesadmin
 and let qubesd validate (errors → diagnostics). Reuse the existing generic helpers in
 qubes_provider/client.py (enforce_properties, read_property, _default_str, _str,
 _present, concrete, as_dict, as_set).

 Verified API facts:
 - vm.volumes[name].resize(bytes) (grow-only; qubesd rejects shrink) and .size → int bytes.
 Volume names: private, root, volatile, kernel.
 - app is a qubesadmin.base.PropertyHolder (app.py:166) — global prefs (default_template,
 default_dispvm, default_netvm, clockvm, …) use the same setattr / property_is_default
 / property_get_default as a VM. So enforce_properties(app, app, props) and
 read_property(app, app, key, desired) work as-is (incl. *default*).
 - vm.devices[bus].attach/detach(DeviceAssignment(backend_domain, ident, options, persistent, frontend_domain, devclass)); .assignments(persistent=None) yields current assignments.
 Bus ∈ pci/usb/block/mic. Assignment identity = (backend_domain, ident).

 #3 — Volume resize (attribute on qubes_vm)

 Schema (resources/vm.py): add volume_size = Map(String), optional+computed.
 Keys are volume names, values human sizes ("10GiB", "2048MiB", or plain bytes).

 Client (client.py):
 - parse_size(s) -> int: accept GiB/MiB/GB/MB/G/M/K/B and bare integers → bytes. Raise
 QubesProviderError on garbage.
 - enforce_volumes(vm, planned: dict): for each {name: size}, want = parse_size(size),
 have = vm.volumes[name].size; if want > have → vm.volumes[name].resize(want); if
 want < have → QubesProviderError ("cannot shrink volume … (qubesd allows grow only)");
 equal → no-op. Map KeyError (no such volume) → diagnostic.
 - read_volume_sizes(vm, desired: dict) -> dict: for each requested name, echo the desired
 string when vm.volumes[name].size >= parse_size(desired) (grow-only "at least N" semantics,
 avoids perpetual diffs from allocation rounding); otherwise return str(actual_bytes) to force
 a resize on next apply.

 Wiring (resources/vm.py): call enforce_volumes inside _enforce_all (after properties).
 Keep reads out of shared read_vm_state (mirrors _with_flags, avoids the data-source
 attribute-count parity footgun): add _with_volumes(state, vm, src) and apply it in
 create/read/update alongside _with_flags. (read re-fetches app.domains[name], cached.)

 #4 — Global preferences (new singleton resource qubes_preferences)

 New file resources/preferences.py, registered in provider.py:get_resources().

 Schema: properties = Map(String) optional+computed; id = String computed (constant
 "dom0" so Terraform has a stable identity; the resource is effectively a singleton).

 Client: read_prefs_state(app, desired) -> dict = {k: read_property(app, app, k, v)} over
 the desired keys (drop None), plus id="dom0".

 CRUD (thin, reusing existing generic enforcement on the app holder):
 - create/update: enforce_properties(app, app, as_dict(planned["properties"])); return
 read_prefs_state(app, planned["properties"]) (+id). *default* works via app.property_get_default.
 - read: read_prefs_state(app, current["properties"]).
 - delete: no-op (drop from state only — destroying the TF resource must not mutate dom0
 globals). Document this.
 - no import beyond reading current values; import_ sets id="dom0" and reads declared keys.

 Note: enforce_properties already skips RESERVED_PROPS; none of the global pref names collide,
 so it is safe to reuse unchanged.

 #8 — Device assignment (new resource qubes_device)

 New file resources/device.py, registered in provider.py.

 Schema (one persistent assignment per resource):
 - vm (frontend qube) — required, requires_replace.
 - devclass (pci/usb/block/mic) — required, requires_replace.
 - backend_domain — required, requires_replace.
 - ident — required, requires_replace.
 - options — Map(String), optional+computed.
 - persistent — Bool, optional (default true; IaC wants attachments that survive reboot).
 - id — String computed = f"{vm}+{devclass}+{backend_domain}+{ident}".

 Client helpers (client.py):
 - _find_assignment(vm, devclass, backend, ident): scan vm.devices[devclass].assignments(),
 match on str(a.backend_domain) == backend and a.ident == ident; return it or None.
 - attach_device(app, cfg) / detach_device(app, cfg): build
 qubesadmin.devices.DeviceAssignment(backend_domain=app.domains[backend], ident=ident, options=as_dict(options), persistent=persistent, devclass=devclass) and call
 app.domains[vm].devices[devclass].attach/detach(...). Wrap qubesadmin.exc.QubesException
 → QubesProviderError.

 CRUD:
 - create: attach_device. (qubesd enforces availability / "frontend must be halted" for pci →
 surfaces as diagnostic, consistent with the rest of the provider.)
 - read: _find_assignment; None → return None (drift → TF recreates). Echo back
 options/persistent from the live assignment.
 - update: identity fields are requires_replace; only options/persistent are mutable →
 detach_device then attach_device.
 - delete: detach_device.

 Test fakes (tests/fakes.py)

 - Extract FakeVM's property machinery (_props/_defaults, __getattr__, __setattr__,
 property_is_default, property_get_default) into a small FakePropertyHolder mixin so
 FakeApp can reuse it for global prefs (default_template/default_dispvm with
 default-following + *default*).                                                                                                                                                     │
 - FakeVM: add volumes = {"private": FakeVolume(bytes), "root": FakeVolume(bytes)} where                                                                                             │
 FakeVolume has .size and .resize() (records new size; rejects shrink only if we want to                                                                                             │
 test the diagnostic — keep resize permissive, let enforce_volumes guard).                                                                                                           │
 - FakeVM: add devices = dict-of-FakeDeviceCollection with attach/detach/assignments                                                                                                 │
 backed by an in-memory list.                                                                                                                                                        │
 - FakeApp: gain default_template/default_dispvm global props (via the mixin) and keep                                                                                               │
 domains for backend/frontend lookup.                                                                                                                                                │
                                                                                                                                                                                     │
 Tests (tests/test_vm.py, or split new test_preferences.py / test_device.py)                                                                                                         │
                                                                                                                                                                                     │
 - Volumes: grow (want>have → resize called, readback echoes desired); idempotent                                                                                                    │
 (want==have → no resize); shrink (want<have → diagnostic); unknown volume name → diagnostic;                                                                                        │
 size parsing ("1GiB"→1073741824, bytes passthrough, bad string error).                                                                                                              │
 - Preferences: set default_template (writes app prop, reads back); *default* round-trips                                                                                            │
 (no write when already default); update changes value; delete is a no-op (dom0 prop unchanged);                                                                                     │
 schemas build; registered in provider.                                                                                                                                              │
 - Device: attach (assignment appears); read finds it / returns None when absent; update                                                                                             │
 options → detach+attach; delete → detach; id format; schemas build; registered in provider.                                                                                         │
                                                                                                                                                                                     │
 Docs (README.md)                                                                                                                                                                    │
                                                                                                                                                                                     │
 - qubes_vm: document volume_size (grow-only, "at least N" semantics).                                                                                                               │
 - New "Global preferences" section with qubes_preferences example (default_template/default_dispvm).                                                                                │
 - New "Devices" section with qubes_device example (e.g. block device → sd-devices).                                                                                                 │
                                                                                                                                                                                     │
 Verification                                                                                                                                                                        │
                                                                                                                                                                                     │
 - .venv/bin/pytest -q — all existing 24 + new cases green (no Qubes needed; fakes cover it).                                                                                        │
 - .venv/bin/python -c "from qubes_provider.provider import QubesProvider as P; \ print([r.get_name() for r in P().get_resources()])" → ['vm','preferences','device'];               │
 each get_schema().to_pb() builds.                                                                                                                                                   │
 - Real (dom0 / mgmt qube, best-effort given sandbox policy limits): in examples/, a slice that                                                                                      │
 (a) qubes_preferences sets default_dispvm, (b) qubes_vm with volume_size = { private = "5GiB" }, (c) qubes_device attaches a block device — tofu apply then re-plan = no changes    │
 (idempotent). Note: schema change on qubes_vm (added volume_size) — since nothing is                                                                                                │
 deployed, clear local examples/**/terraform.tfstate and re-apply rather than writing an                                                                                             │
 upgrader (per prior decision).                                                                                                                                                      │
                                                                                                                                                                                     │
 Out of scope (explicitly)                                                                                                                                                           │
                                                                                                                                                                                     │
 - Volume revisions_to_keep, pool selection, clone-of-volume (could be a later volume_size→                                                                                          │
 volume {} block expansion).                                                                                                                                                         │
 - Non-persistent (hot) device attach as a distinct lifecycle; we model persistent assignments.                                                                                      │
 - Resetting global prefs on destroy (deliberately a no-op).



 NEW:
   - import existing VMs as resources (created-by-$vmmname)