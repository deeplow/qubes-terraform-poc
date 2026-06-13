
Phase 1: `qubes_vm` resource (full CRUD + import) and
Planned next:

  - services (+ensure restart to apply)
  - `qubes_vm` data source (to understand system state)?
  - RPC policies
  - `qubes_firewall`
  - devices working
  - [ ] qvm-template important things:
      - [ ] understand how qubes_vm.origin with requires_replace impacts existing systems or previously modified templates
      - [ ] build in mechanism
      - [ ] propose changes to qvm-template to allow qubes.TemplateSearch be performed directly on management qube
         - or simply allowing @default, for example (currently it just errors before even making an admin API call)
  - detect and apply ansible when source changes
  - [ ] CI against both `terraform` and `tofu`.
  - [ ] qvm-template install
  - [ ] improve terraform-ansible integration
    - better way to designate that a qube can do ansible configuration. Maybe dedicated datasource?
  - [ ] packaging
  - [ ] clean up README