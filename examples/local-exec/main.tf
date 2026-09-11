# Run commands inside a qube with Terraform's built-in local-exec provisioner.
#
# The provider has no resource for executing commands inside qubes, so this is
# the workaround: local-exec runs qvm-run on the Terraform host. `--pass-io`
# makes qvm-run exit with the command's own exit status, so a failing command
# fails the apply; `--user` selects the account the command runs as.
#
# Run from this directory with the dev_overrides CLI config:
#   TF_CLI_CONFIG_FILE=../qubes.tfrc tofu apply

terraform {
  required_providers {
    qubes = {
      source = "deeplow/qubes"
    }
  }
}

provider "qubes" {}

resource "qubes_vm" "exec_demo" {
  name     = "tf-exec-demo"
  klass    = "AppVM"
  template = "*default*"
  label    = "blue"
}

resource "terraform_data" "exec_demo_setup" {
  # Re-run the command whenever the qube is replaced.
  triggers_replace = [qubes_vm.exec_demo.name]

  provisioner "local-exec" {
    command = "qvm-run --pass-io --user=user ${qubes_vm.exec_demo.name} 'test -d /home/user'"
  }
}
