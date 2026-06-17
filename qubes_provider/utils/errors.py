# SPDX-License-Identifier: GPL-3.0-or-later
"""
Provider-level exception, in its own module so every layer can share it
without an import cycle.
"""


class QubesProviderError(Exception):
    """Raised for provider-level failures (surfaced as Terraform diagnostics)."""
