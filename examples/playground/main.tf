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
  klass = "AppVM"
  label    = "red"
  template = "*default*"

  properties = {
    netvm                = "" # no network
    template_for_dispvms = "True"
  }

  tags = ["tf-playground"]

  # Grow the private volume. Size is in bytes (matching qubes-ansible).
  # volumes = {
  #   private = { size = "10368709120" } # 5 GiB
  # }

  # If the qube is running when its template changes, halt it first.
  shutdown_if_required = true
}

# A DispVM based on alpha (which is a dispvm template via the property above).
resource "qubes_vm" "beta" {
  name     = "tf-playground-beta"
  klass = "DispVM"
  label    = "purple"
  template = qubes_vm.alpha.name

  # If the qube is running when its template changes, halt it first.
  shutdown_if_required   = true
  force_shutdown       = true
}

output "alpha" {
  value = {
    name        = qubes_vm.alpha.name
    template    = qubes_vm.alpha.template # "*default*"
    properties  = qubes_vm.alpha.properties
    tags        = qubes_vm.alpha.tags
  }
}

output "beta_name" {
  value = qubes_vm.beta.name
}
