# Developer convenience targets for the Qubes Terraform/OpenTofu provider.

PYTHON ?= ./.venv/bin/python

QUBES_ANSIBLE_URL ?= https://github.com/QubesOS/qubes-ansible
VENDOR := qubes_provider/utils/qubes_ansible
UP := ansible_collections/qubesos/core/plugins

.PHONY: test update-qubes-ansible

test:                      ## Run the unit test suite
	$(PYTHON) -m pytest

# Re-vendor only the qubes-ansible files the provider imports, from the latest
# upstream. The files are kept flat under $(VENDOR); the one intra-collection
# import in qubes_module_qube.py is rewritten to match the flat layout.
update-qubes-ansible:      ## Re-vendor the used qubes-ansible files from the latest upstream
	tmp=$$(mktemp -d); \
	git clone --quiet --depth 1 $(QUBES_ANSIBLE_URL) $$tmp; \
	install -D -m 644 "$$tmp/$(UP)/modules/qube_facts.py"             "$(VENDOR)/qube_facts.py"; \
	install -D -m 644 "$$tmp/$(UP)/module_utils/qubes_module_qube.py" "$(VENDOR)/qubes_module_qube.py"; \
	install -D -m 644 "$$tmp/$(UP)/module_utils/qubes_helper.py"      "$(VENDOR)/qubes_helper.py"; \
	sed -i 's#^from ansible_collections\.qubesos\.core\.plugins\.module_utils\.qubes_helper import#from qubes_helper import#' "$(VENDOR)/qubes_module_qube.py"; \
	rm -rf "$$tmp"; \
	echo "Vendored 3 file(s) from $(QUBES_ANSIBLE_URL) (latest)"
