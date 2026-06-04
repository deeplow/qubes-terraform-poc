terraform {
  required_providers {
    qubes = {
      source = "deeplow/qubes"
    }
  }
}

provider "qubes" {}

# Fully self-contained: depends on NO pre-existing qube.
#   - TemplateVM with no `template`  -> does not reference any TemplateVM
#   - netvm = ""                       -> no network, does not reference a NetVM
#   - label is a universal color, not a qube
resource "qubes_vm" "alpha" {
  name     = "tf-playground-alpha"
  vm_class = "AppVM"
  label    = "red"
  netvm    = ""
  template = "*default*"
  template_for_dispvms = true
}

resource "qubes_vm" "beta" {
  name     = "tf-playground-beta"
  vm_class = "DispVM"
  label    = "green"
  template = qubes_vm.alpha.name
}

# Read one of them back through the data source.
data "qubes_vm" "alpha" {
  name       = qubes_vm.alpha.name
  depends_on = [qubes_vm.alpha]
}

output "alpha" {
  value = {
    name        = qubes_vm.alpha.name
    vm_class    = qubes_vm.alpha.vm_class
    label       = qubes_vm.alpha.label
    template    = qubes_vm.alpha.template # null: no template
    netvm       = qubes_vm.alpha.netvm    # "": no network
    memory      = qubes_vm.alpha.memory
    power_state = data.qubes_vm.alpha.power_state
  }
}

output "beta_name" {
  value = qubes_vm.beta.name
}
