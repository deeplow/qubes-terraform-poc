# terraform-provider-qubes

A Terraform / OpenTofu provider for **Qubes OS**, written in Python and bound directly
to the Qubes **Python Admin API** (`qubesadmin`). Manage qubes declaratively:

```hcl
resource "qubes_vm" "work" {
  name     = "tf-work"
  klass = "AppVM"
  label    = "blue"
  template = "*default*"

  properties = {
    memory = "2048"
    netvm  = "sys-firewall"
  }
  services = ["qubes-firewall"]
  tags     = ["team-x"]
  volumes  = { private = { size = "21474836480" } } # bytes (20 GiB)
}
```

## How it works

Terraform/OpenTofu providers are plugins that speak a gRPC-based protocol over the
HashiCorp go-plugin handshake (historically Go-only). This provider uses
[**python-tf**](https://github.com/hfern/tf) (`pip install tf`, MIT), a pure-Python
implementation of **Terraform plugin protocol v6**, so the plugin can be written in Python.

Rather than re-implement Qubes VM lifecycle logic, the provider **delegates** to the bundled
[**qubes-ansible**](https://github.com/QubesOS/qubes-ansible) collection (a git submodule under
`qubes_provider/helpers/qubes_ansible/`), driving its `QubeModule` (writes) and `qube_facts`
(reads) in-process. A single **Adapter** (`qubes_adapter.QubesVmAdapter`) is the seam: it
translates Terraform state ⇄ qube params/facts and runs those modules through an
`AnsibleModuleShim` that supplies the only `AnsibleModule` contract they use
(`params` / `fail_json` / `exit_json`) — **no Ansible runtime is installed or needed**.
qubes-ansible in turn talks to `qubesadmin`, and `qubesd` remains the source of truth for types,
defaults and constraints.

```
OpenTofu/Terraform CLI
   │  go-plugin handshake + gRPC (protocol v6, mTLS)
   ▼
terraform-provider-qubes  (this Python process)
   │  qubes_adapter.QubesVmAdapter: state <-> params/facts, via AnsibleModuleShim
   ▼
qubes-ansible collection  (bundled submodule, GPL-3.0)
   │  import qubesadmin; app = qubesadmin.Qubes()
   ▼
qubesd  (local socket in dom0, OR qrexec admin.vm.* from a management qube)
   ▼
libvirtd / qubes.xml
```

`qubesadmin.Qubes()` auto-detects its transport, so the same provider works in **dom0**
(local `qubesd` socket) or in a **management qube** (qrexec, gated by Admin API policy).

CRUD calls the adapter, which delegates to qubes-ansible:

| Terraform | adapter method → qubes-ansible | Notes |
|---|---|---|
| create / update | `create_or_update` → `QubeModule.run()` (`state: present`) | create or clone, then enforce properties/volumes/devices/features/tags/notes; update also prunes de-declared tags/features/services |
| read | `read_vm` → `qube_facts.core()` | projected onto the declared subset; `"*default*"` round-trips |
| delete | `delete` → `QubeModule.run()` (`state: absent`) | |
| import | `terraform import qubes_vm.x <name>` | reads the qube into state |

The genuinely Terraform-specific behavior lives in the adapter (or, for rollback, the resource):
projecting reads onto the *declared* subset (no spurious diffs), **converging removals**
(qubes-ansible only adds tags/features/services; Terraform removes ones dropped from config), and
rolling back a failed create.

## The `qubes_vm` resource

Identity (`name`, `klass`, `label`) is typed; everything else is a **generic bag** passed
straight through qubes-ansible to `qubesadmin` and validated by **qubesd** — so the provider
doesn't hardcode (or need to track) individual properties.

| Attribute | Type | Notes |
|---|---|---|
| `name` | string, required | Unique VM name. Changing it replaces the resource. |
| `klass` | string, required | `AppVM`, `TemplateVM`, `StandaloneVM`, `DispVM`. Replaces on change. |
| `label` | string, required | Label color (red, blue, …). |
| `template` | string, optional/computed | Base template. `"*default*"` = Qubes default; a name = that template. |
| `clone_src` | string, optional | Create this qube by **cloning** an existing one (its volumes + prefs) instead of from a template. Replaces on change. |
| `properties` | map(string), optional/computed | **Any** qube property: `memory`, `maxmem`, `netvm`, `kernel`, `virt_mode`, `autostart`, `template_for_dispvms`, `guivm`, … Values are strings (Qubes-canonical, e.g. `"True"`); `"*default*"` = that property's current Qubes default; `""` clears a VM-valued property. |
| `features` | map(string), optional | Qube features (`vm.features`). Config-authoritative: deleting/clearing the block removes the declared keys (system-set features untouched). |
| `services` | set(string), optional | Qubes services to enable — sugar for the `service.<x>` feature. Config-authoritative: deleting/clearing the block disables the declared services. |
| `tags` | set(string), optional | Tags this resource manages. Config-authoritative: deleting/clearing the block removes the declared tags. Qubes auto-tags (`created-by-*`) are left alone and never reported. |
| `volumes` | map(map(string)), optional/computed | Per-volume config, e.g. `{ private = { size = "5368709120" } }`. Size is in **bytes** (matching qubes-ansible); volumes are **grow-only**. Also `revisions_to_keep`. |
| `devices` | json, optional | Device assignments in qubes-ansible's raw form: a list of `"class:backend:port:devid"` specs, or `{ strategy = "strict"\|"append", items = [...] }`. |
| `notes` | string, optional/computed | Free-form qube notes. |
| `shutdown_if_required` | bool, optional | If changing `template` needs the qube halted and it's running, shut it down first. Default false → error instead. |
| `force_shutdown` | bool, optional | Force the shutdown done for a template change. |

`clone_src`, `services`, `volumes`, `devices` and `notes` are all backed by
qubes-ansible's `QubeModule`. Tags, features and services are **converged**: declaring one adds
it, and removing it from the config removes it from the qube (Qubes auto-tags are never touched).

Changing a running qube's `template` requires it halted; qubesd rejects it otherwise. With
`shutdown_if_required = true` the provider shuts the qube down first (qubes-ansible's
`_shutdown_for_template_update`); otherwise the apply fails with *"Cannot change the template
while the qube is running."*

### Design: delegate, don't re-implement

Qubes VM lifecycle has real nuances (must-be-halted-to-change-X, clone vs create, default
sentinels, device assignment modes). Rather than copy them, this provider drives the
**qubes-ansible** collection, which already encodes them, and lets **qubesd** validate. New
qube properties work with **zero** plugin changes — `properties` is a generic name→value bag.

`"*default*"` (the qubes-ansible/Salt sentinel) lets Qubes choose a property's value and
round-trips with no spurious diffs (via `qube_facts`' `default_properties`). The provider's own
code is just the Terraform-shaped layer on top: a managed-subset read projection, declarative
removal of de-declared tags/features/services, create rollback, and the gRPC/schema plumbing.

## Requirements

- **Python 3.11+**.
- **`qubesadmin`** — shipped by Qubes (`qubes-core-admin-client`), present in **dom0** and available
  in management qubes. It is *not* on PyPI, so it lives in the **system** Python; the venv
  must be allowed to see system site-packages (see Install). Device assignment uses the newer
- `qubes-desktop-linux-common` — Needed for cloning appmenus when performing a qube clone.
  `qubesadmin.device_protocol` API (Qubes **4.3+**); other features work on older qubesadmin.
- The bundled **qubes-ansible** git submodule (no Ansible runtime required — the provider only
  imports the collection's Python and supplies its own `AnsibleModule` shim). After cloning:
  `git submodule update --init`.
- Terraform ≥ 1.0 or any OpenTofu (both support plugin protocol v6).

## Install

`qubesadmin` lives in the system Python, so the venv needs access to system site-packages
(the venv's own deps still take precedence over system ones):

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
# For an existing venv instead: set `include-system-site-packages = true` in .venv/pyvenv.cfg
.venv/bin/python -c "import qubesadmin"   # should succeed on dom0 / a management qube
# entrypoint installed as .venv/bin/terraform-provider-qubes
```

## Running it

Because this provider is not published to a registry, use one of:

### Option A — `--dev` reattach (best for iteration)

```bash
.venv/bin/terraform-provider-qubes --dev
# prints:  export TF_REATTACH_PROVIDERS='{...}'
```
Copy that `export` into another shell, then run `tofu plan` / `tofu apply` there. No
`init`, no installation.

### Option B — `dev_overrides`

`TF_CLI_CONFIG_FILE` must point at the **CLI config** (`.tfrc`, the dev_overrides file in
`examples/qubes.tfrc`) — **never** at a `.tf` file. Run from the directory that contains
the `.tf` config:

```bash
cd examples/playground
export TF_CLI_CONFIG_FILE=$PWD/../qubes.tfrc   # the .tfrc, not main.tf; works for terraform and tofu
tofu plan        # no `init` needed under dev_overrides
tofu apply
```

Or just use the bundled Makefile, which sets all of that for you (works from any cwd):

```bash
make -C examples/playground plan     # also: apply, destroy
```

To avoid the env var entirely, copy the `dev_overrides { ... }` block from
`examples/qubes.tfrc` into `~/.tofurc` (OpenTofu's default CLI config); then plain
`tofu plan` works.

### Option C — local filesystem mirror (allows lockfiles / `init`)

`tf` ships `tf.runner.install_provider(host, namespace, project, version, plugin_dir, provider_script)`
which builds a local mirror you can consume with a normal `source` + `tofu init`.

## Running from a management qube (qrexec Admin API policy)

When run outside dom0, `qubesadmin` reaches `qubesd` over qrexec, gated by dom0 policy in
`/etc/qubes/policy.d/` (e.g. `30-qubes-tf.policy`). Grant the management qube only the
verbs this provider uses:

```
# Assuming 'work' is your management qube
admin.vm.Create.AppVM	*	work		@adminvm	target=dom0
admin.vm.List	      	*	work		@tag:created-by-work	target=dom0
admin.vm.Remove	   	*	work		@tag:created-by-work	target=dom0
admin.vm.property.Get	*	work		@tag:created-by-work	target=dom0
admin.vm.property.Set	*	work		@tag:created-by-work	target=dom0
admin.vm.CurrentState	*	work		@tag:created-by-work	target=dom0
```

The capabilities added on top need their own verbs as you use them: `admin.vm.volume.Resize`
(volumes), `admin.vm.device.*.Attach`/`Detach`/`List` (devices), and `admin.vm.Clone`
(`clone_src`).

Scope as tightly as your use case allows. Otherwise, the following `include/admin-global-ro` may help:

```
work    @adminvm              allow   target=@adminvm
work    @tag:created-by-work  allow   target=@adminvm
work    work                  allow   target=@adminvm
```

`include/admin-global-rwx`:
```
work	@tag:created-by-work	allow	target=@adminvm
```

## Development & tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q          # no Qubes required: the suite drives the real qubes-ansible
                             # QubeModule/qube_facts against in-memory fakes
```

The tests run the bundled qubes-ansible modules against fakes (`tests/fakes.py`,
`install_qube_fakes`), so they exercise the actual delegation path without a live `qubesd`.

Smoke-test the plugin handshake (should print `1|6|unix|<sock>|grpc|<cert>`):

```bash
.venv/bin/terraform-provider-qubes   # Ctrl-C to stop
```

## Status

`qubes_vm` resource (full CRUD + import), delegating to
qubes-ansible: properties, features, services, tags, volumes, devices, notes, and clone.
Planned next: dom0 global preferences (`default_template`/`default_dispvm`),
`qubes_firewall`, and CI against both `terraform` and `tofu`.

## Re-implementation

This is just a PoC and should be implemented with a better foundation (and vetting of used components / dependencies)

Lessons for a rewrite:
  - consider doing as an [external provider](https://registry.terraform.io/providers/hashicorp/external/latest/docs/data-sources/external)
  - re-evaluate provider framework (or use go)
  - well scoped permissions
  - use qubes-ansible proxy strategy
  - don't base on top of ansible module (done here for simplicitly), but when possible use or create higher level
    utils functions in qubes-core-admin-client
  - Better organized Data Sources: either follow admin API or some pre-established namespace for the various tools


## License

GPL-3.0-or-later.

This provider delegates its VM lifecycle logic to the bundled
[qubes-ansible](https://github.com/QubesOS/qubes-ansible) collection (under
`qubes_provider/helpers/qubes_ansible/`), which it imports and runs in-process. qubes-ansible is
licensed GPL-3.0-or-later, so the provider is too. The bundled submodule retains its own copyright
and license headers.
