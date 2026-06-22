
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
      - [ ] installing template with another name (even if the original one exists). Example: downloads debian-12-xfce and saves it as deb-12-sd
  - detect and apply ansible when source changes
  - [ ] CI against both `terraform` and `tofu`.
  - [ ] qvm-template install
  - [ ] improve terraform-ansible integration
    - better way to designate that a qube can do ansible configuration. Maybe dedicated datasource?
  - [ ] packaging
  - [ ] importing existing qubes (generic qubes provider) — ignore the defaults
  - [ ] clean up README

Future ideas:
- RPC policies defined in the resources rather than as just a policy file
- Threat modeling goes hand in hand with system architecture (see: threatcl)
- Basis for self-service infrastructure (blueprints for user's own configurations)