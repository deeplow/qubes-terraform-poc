# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``qubes_policy`` resource: one qrexec policy *file* managed declaratively.

Like ``qubes_prefs`` (and unlike ``qubes_vm``), this talks to a dom0/global admin
API directly rather than through qubes-ansible — here the qrexec **policy admin
API**, via the shared :mod:`qubes_provider.policy` core. One resource maps to one
file under ``/etc/qubes/policy.d/`` (its ``name`` is the file stem; the leading
number orders evaluation, lower wins).

Two authoring modes, mutually exclusive (exactly one required):

- ``content``: the raw policy file text — typically a ``templatefile()`` of qrexec
  lines (``service argument source target action [opts]``), a ``file()``, or a
  heredoc. Terse; each line is the positional rule. Written ~verbatim.
- ``rules``: an ordered list of rule maps, each with an explicit ``source``. Gives
  per-field structure / programmatic generation.
"""

from typing import Optional

from tf import schema, types
from tf.iface import (
    CreateContext,
    DeleteContext,
    ImportContext,
    ReadContext,
    UpdateContext,
)
from tf.provider import Resource
from tf.types import Unknown
from tf.utils import Diagnostics

from ..utils import policy as policy_core
from ..utils.policy import QubesPolicy


def _is_set(value) -> bool:
    """True if a config/state value is a real value (not null / not Unknown)."""
    return value is not None and value is not Unknown


class QubesPolicyResource(Resource):
    """Maps a Terraform resource onto one qrexec policy file, via QubesPolicy.

      create/update -> policy.write_text (validate + Replace)
      read          -> echo declared when the file canonically matches, else the file
      delete        -> QubesPolicy.remove
    """

    def __init__(self, provider, backend=None):
        self.provider = provider
        self.qubes = backend or QubesPolicy()  # injectable for tests

    @classmethod
    def get_name(cls) -> str:
        # Combined with the provider model prefix "qubes_" -> "qubes_policy".
        return "policy"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        return schema.Schema(
            version=1,
            attributes=[
                schema.Attribute(
                    "name", types.String(), required=True, requires_replace=True,
                    description=(
                        "Policy file stem under /etc/qubes/policy.d/ (letters, digits, "
                        "underscore, hyphen). The leading number orders evaluation "
                        '(lower wins), e.g. "31-securedrop-workstation".'
                    ),
                ),
                schema.Attribute(
                    "content", types.String(), optional=True,
                    description=(
                        "Raw qrexec policy text (mutually exclusive with `rules`; set "
                        "exactly one). Typically templatefile()/file()/a heredoc of lines "
                        "`service argument source target action [opts]` (argument is "
                        'usually "*"). Validated by qubesd; written verbatim.'
                    ),
                ),
                schema.Attribute(
                    "rules", types.List(types.Map(types.String())), optional=True,
                    description=(
                        "Ordered qrexec rules (mutually exclusive with `content`; set "
                        "exactly one; first match wins). Each rule is a string map: "
                        'required "service", "source", "target", "action" (allow|deny|ask); '
                        'optional "argument" (default "*"), "redirect" (the policy target= '
                        'override), "default_target", "user", "notify" (yes|no). Endpoints '
                        'may be a qube name, "@tag:x", "@anyvm", "@default", "dom0" or '
                        '"@dispvm:x".'
                    ),
                ),
                schema.Attribute(
                    "id", types.String(), computed=True,
                    description="Policy file stem (equals name).",
                ),
            ],
        )

    # --- validation ---------------------------------------------------------

    def validate(self, diags: Diagnostics, type_name: str, config: dict):
        # A value present in config is non-null (an unknown-but-set value is the
        # Unknown sentinel, also non-null); an omitted optional is null.
        has_content = config.get("content") is not None
        has_rules = config.get("rules") is not None
        if has_content == has_rules:
            diags.add_error(
                "Invalid qubes_policy configuration",
                'Set exactly one of "content" or "rules".',
            )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _state(name: str, *, rules: Optional[list] = None,
               content: Optional[str] = None) -> dict:
        return {"name": name, "id": name, "rules": rules, "content": content}

    def _text(self, data: dict) -> str:
        """The policy file text for either mode."""
        if _is_set(data.get("content")):
            return data["content"]
        return policy_core.render_file(data["rules"], default_source=None)

    def _echo(self, name: str, data: dict) -> dict:
        """State echoing whichever mode was declared (the other field stays null)."""
        if _is_set(data.get("content")):
            return self._state(name, content=data["content"])
        return self._state(name, rules=data["rules"])

    # --- CRUD ---------------------------------------------------------------

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        name = planned["name"]
        try:
            policy_core.write_text(self.qubes, name, self._text(planned))
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to write qrexec policy", f"{name}: {exc}")
            return None
        return self._echo(name, planned)

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
        name = current["name"]
        try:
            if _is_set(current.get("content")):
                content = policy_core.read_text_state(self.qubes, name, current["content"])
                return None if content is None else self._state(name, content=content)
            rules = policy_core.read_state(
                self.qubes, name, current.get("rules"), None, include_source=True
            )
            return None if rules is None else self._state(name, rules=rules)
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to read qrexec policy", f"{name}: {exc}")
            return None

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> Optional[dict]:
        name = planned["name"]
        try:
            policy_core.write_text(self.qubes, name, self._text(planned))
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to update qrexec policy", f"{name}: {exc}")
            return None
        return self._echo(name, planned)

    def delete(self, ctx: DeleteContext, current: dict):
        try:
            self.qubes.remove(current["name"])
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to remove qrexec policy", str(exc))

    def import_(self, ctx: ImportContext, id: str) -> Optional[dict]:
        """Adopt an existing policy file into state by its stem (as raw ``content``)."""
        try:
            content = policy_core.read_text_state(self.qubes, id, None)
            if content is None:
                ctx.diagnostics.add_error("No such policy file", f"Cannot import {id!r}.")
                return None
            return self._state(id, content=content)
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to import qrexec policy", str(exc))
            return None
