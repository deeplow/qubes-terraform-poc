terraform {
  required_providers {
    qubes = {
      source = "deeplow/qubes"
    }
  }
}

provider "qubes" {}

# An AppVM on the default template; properties is a generic bag passed to qubesd.
resource "qubes_vm" "work_demo" {
  name     = "tf-work-demo"
  klass = "AppVM"
  template = "*default*"
  label    = "blue"

  properties = {
    memory = "2048"
    netvm  = "*default*"
  }
}

output "work_template" {
  value = qubes_vm.work_demo.template
}

output "work_memory" {
  value = qubes_vm.work_demo.properties["memory"]
}
