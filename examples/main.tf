terraform {
  required_providers {
    qubes = {
      source = "deeplow/qubes"
    }
  }
}

provider "qubes" {}

# Create an AppVM based on the fedora-40 template, on the firewall network.
resource "qubes_vm" "work_demo" {
  name     = "tf-work-demo"
  vm_class = "AppVM"
  template = "fedora-43-xfce"
  label    = "blue"
  memory   = 2048
  netvm    = "*default*"
}

# Read an existing qube.
#data "qubes_vm" "firewall" {
#  name = "*default*"
#}

output "work_template" {
  value = qubes_vm.work_demo.template
}

output "firewall_power_state" {
  value = data.qubes_vm.firewall.power_state
}
