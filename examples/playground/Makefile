# Convenience runner for the Qubes provider playground.
#
# Wires OpenTofu to the local (dev_overrides) provider and the project's tofu
# binary, with paths resolved relative to THIS Makefile so it works from any cwd:
#   make plan      (or: make -C examples/playground plan)
#   make apply
#   make destroy
#
# TF_CLI_CONFIG_FILE must point at the .tfrc CLI config (dev_overrides), NOT a .tf.
export TF_CLI_CONFIG_FILE := $(CURDIR)/../qubes.tfrc
TOFU ?= $(CURDIR)/../../.venv/bin/tofu

.PHONY: plan apply destroy
plan:    ; $(TOFU) plan
apply:   ; $(TOFU) apply -auto-approve
destroy: ; $(TOFU) destroy -auto-approve
