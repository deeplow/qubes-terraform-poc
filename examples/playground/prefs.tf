# Manage dom0 global preferences (the `qubes-prefs` surface) directly via the Qubes
# Admin API. Each attribute is one global property: set it to enforce the value,

# Disabled, since this requires an extra RPC policy
#resource "qubes_prefs" "this" {
#  default_qrexec_timeout = "120"
#}

#output "qrexec_timeout" {
#  value = qubes_prefs.this.default_qrexec_timeout
#}
