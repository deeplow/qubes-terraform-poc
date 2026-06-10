# (terraform/provider blocks live in main.tf — this file composes with it.)

# System-wide (dom0) data sources — no inputs; resolved at plan time.
# Type names mirror the Qubes Admin API namespaces (admin.property/label/pool/
# vmclass/deviceclass).
data "qubes_property"    "this" {}
data "qubes_label"       "all" {}
data "qubes_pool"        "all" {}
data "qubes_vmclass"     "all" {}
data "qubes_deviceclass" "all" {}

# Reuse the dom0 default template instead of hardcoding a name. Each global
# property is its own typed attribute, e.g. data.qubes_property.this.default_template
resource "qubes_vm" "work" {
  name     = "tf-ds-work"
  klass    = "AppVM"
  label    = "blue"
  template = data.qubes_property.this.default_template
}

output "default_netvm" {
  value = data.qubes_property.this.default_netvm
}

output "label_colors" {
  value = { for name, l in data.qubes_label.all.labels : name => l.color }
}

output "thin_pools" {
  value = [for name, p in data.qubes_pool.all.pools : name if p.driver == "lvm_thin"]
}

output "vm_pool_free_bytes" {
  value = (
    tonumber(data.qubes_pool.all.pools["vm-pool"].size) -
    tonumber(data.qubes_pool.all.pools["vm-pool"].usage)
  )
}

output "device_classes" {
  value = data.qubes_deviceclass.all.names
}
