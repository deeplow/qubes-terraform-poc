terraform {
  required_providers {
    qubes = {
      source = "deeplow/qubes"
    }
  }
}

provider "qubes" {}

# Self-contained: depends on NO pre-existing qube.
#   - template = "*default*"  -> the Qubes default template (no hardcoded name)
#   - properties / features / tags are generic bags passed straight to qubesd;
#     values are strings, "*default*" means the property's current Qubes default.
resource "qubes_vm" "alpha" {
  name     = "tf-playground-alpha"
  vm_class = "AppVM"
  label    = "red"
  template = "*default*"

  properties = {
    netvm                = "" # no network
    template_for_dispvms = "True"
  }

  tags = ["tf-playground"]

  # If the qube is running when its template changes, halt it first.
  shutdown_if_required = true
}

# A DispVM based on alpha (which is a dispvm template via the property above).
resource "qubes_vm" "beta" {
  name     = "tf-playground-beta"
  vm_class = "DispVM"
  label    = "purple"
  template = qubes_vm.alpha.name

  # If the qube is running when its template changes, halt it first.
  shutdown_if_required   = true
  force_shutdown       = true
}

data "qubes_vm" "alpha" {
  name       = qubes_vm.alpha.name
  properties = { netvm = "" }
  depends_on = [qubes_vm.alpha]
}

output "alpha" {
  value = {
    name        = qubes_vm.alpha.name
    template    = qubes_vm.alpha.template # "*default*"
    properties  = qubes_vm.alpha.properties
    tags        = qubes_vm.alpha.tags
    power_state = data.qubes_vm.alpha.power_state
  }
}

output "beta_name" {
  value = qubes_vm.beta.name
}
