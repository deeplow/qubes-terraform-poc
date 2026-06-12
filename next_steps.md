
Phase 1: `qubes_vm` resource (full CRUD + import) and
Planned next:

  - services (+ensure restart to apply)
  - `qubes_vm` data source (to understand system state)?
  - RPC policies
  - `qubes_firewall`
  - devices working
  - detect and apply ansible when source changes
  - [ ] CI against both `terraform` and `tofu`.
  - [ ] qvm-template install
  - [ ] improve terraform-ansible integration
    - better way to designate that a qube can do ansible configuration. Maybe dedicated datasource?
  - [ ] packaging
  - [ ] clean up README