# terraform-provider-qubes

A Terraform / OpenTofu provider for **Qubes OS**, written in Python and bound directly
to the Qubes **Python Admin API** (`qubesadmin`). Manage qubes declaratively:

```hcl
resource "qubes_vm" "work" {
  name     = "tf-work"
  vm_class = "AppVM"
  template = "fedora-40"
  label    = "blue"
  memory   = 2048
  netvm    = "sys-firewall"
}
```

## How it works

Terraform/OpenTofu providers are plugins that speak a gRPC-based protocol over the
HashiCorp go-plugin handshake (historically Go-only). This provider uses
[**python-tf**](https://github.com/hfern/tf) (`pip install tf`, MIT), a pure-Python
implementation of **Terraform plugin protocol v6**, so the plugin can be written in
Python and call `qubesadmin` in-process:

```
OpenTofu/Terraform CLI
   │  go-plugin handshake + gRPC (protocol v6, mTLS)
   ▼
terraform-provider-qubes  (this Python process)
   │  import qubesadmin; app = qubesadmin.Qubes()
   ▼
qubesd  (local socket in dom0, OR qrexec admin.vm.* from a management qube)
   ▼
libvirtd / qubes.xml
```

`qubesadmin.Qubes()` auto-detects its transport, so the same provider works in **dom0**
(local `qubesd` socket) or in a **management qube** (qrexec, gated by Admin API policy).

CRUD maps onto the Admin API:

| Terraform | qubesadmin | Admin API |
|---|---|---|
| create | `app.add_new_vm(cls, name, label, template)` | `admin.vm.Create.<class>` |
| read | `app.domains[name]` + properties | `admin.vm.property.Get` |
| update | `vm.<prop> = value` | `admin.vm.property.Set` |
| delete | `del app.domains[name]` | `admin.vm.Remove` |
| import | `terraform import qubes_vm.x <name>` | `admin.vm.List` |

## The `qubes_vm` resource

| Attribute | Type | Notes |
|---|---|---|
| `name` | string, required | Unique VM name. Changing it replaces the resource. |
| `vm_class` | string, required | `AppVM`, `TemplateVM`, `StandaloneVM`, `DispVM`. Replaces on change. |
| `label` | string, required | Label color (red, blue, …). Updatable. |
| `template` | string, optional/computed | Base template. `"*default*"` = Qubes default template; a name = that template. Updatable. |
| `memory` | number, optional/computed | Initial memory (MB). |
| `maxmem` | number, optional/computed | Max memory for ballooning (MB). |
| `netvm` | string, optional/computed | `"*default*"` = Qubes default netvm; `""` = no network; a name = that netvm. |
| `template_for_dispvms` | bool, optional/computed | Whether this VM may serve as a template for DispVMs. |
| `provisioned` | bool, computed | True once created by this provider. |

A `qubes_vm` **data source** exposes the same fields plus `power_state`.

`"*default*"` (the qubes-ansible sentinel) lets Qubes choose the value; it round-trips, so it
produces no spurious diffs. qubesd has no literal `*default*`, and it refuses to *unset* some
properties — e.g. an AppVM's `template` ("Cannot unset template; set it to the current default
instead"). So the provider implements `*default*` as **"set the property to its current
default value"** (resolved via `admin.vm.property.GetDefault`), which is a no-op when the
value already equals the default. On read it reports `"*default*"` when the live value equals
the current default. This needs only `property.Get`/`GetDefault` — no `property.Reset`.

## Requirements

- **Python 3.11+**.
- **`qubesadmin`** — shipped by Qubes (`qubes-core-admin-client`), present in **dom0** and
  in management qubes. It is *not* on PyPI, so it lives in the **system** Python; the venv
  must be allowed to see system site-packages (see Install).
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
.venv/bin/pytest -q          # 14 tests; mocks qubesadmin, no Qubes required
```

Smoke-test the plugin handshake (should print `1|6|unix|<sock>|grpc|<cert>`):

```bash
.venv/bin/terraform-provider-qubes   # Ctrl-C to stop
```

## Status

Phase 1: `qubes_vm` resource (full CRUD + import) and `qubes_vm` data source.
Planned next: broader properties (kernel, virt_mode, autostart, services, tags),
`qubes_firewall`, volumes/devices, and CI against both `terraform` and `tofu`.

## License

MIT.
