# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared core for managing qrexec policy files declaratively.

A qrexec policy *file* (one entry under ``/etc/qubes/policy.d/``) is the unit the
Qubes policy admin API operates on — there is no per-rule API object, only whole
named files (read / replace / remove). This module is the single seam that:

- renders an ordered list of rule maps into the canonical policy-line format
  (:func:`render_file`), and parses a file back into the same minimal maps
  (:func:`parse_to_maps`), reusing ``qrexec.policy.parser`` so our notion of
  "valid" and "canonical" is exactly qubesd's;
- drives the admin API via ``qrexec.policy.admin_client.PolicyClient``
  (:class:`QubesPolicy`, injectable for tests);
- detects drift without spurious diffs (:func:`read_state`): a provider-owned file
  is echoed back as the *declared* maps whenever it still canonically matches what
  we would render, and only re-derived from disk when it genuinely differs.

A rule is a flat ``{str: str}`` map (the provider's ``List(Map(String))`` idiom):
``service`` / ``target`` / ``action`` are required, ``source`` is required only
where it is explicit (the standalone resource); ``argument`` defaults to ``*`` and
``redirect`` (-> ``target=``), ``default_target``, ``user`` and ``notify`` are
optional params.
"""

from typing import Optional

from .errors import QubesProviderError

try:  # qrexec ships in dom0 and in management qubes (and imports on this dev box)
    from qrexec.exc import PolicySyntaxError
    from qrexec.policy.admin_client import PolicyClient
    from qrexec.policy.parser import Deny, StringPolicy
except ImportError as exc:  # pragma: no cover
    raise QubesProviderError(
        "qrexec is not available. Run this provider in dom0 or a Qubes management "
        "qube that has the qubes-core-qrexec package installed."
    ) from exc

_HEADER = "# Managed by the Qubes Terraform provider — do not edit by hand."

# Rule-map params rendered as `key=value`, in a fixed order, mapped to the qrexec
# action-parameter spelling (``redirect`` is the policy's confusingly-named ``target=``).
_PARAMS = (("redirect", "target"), ("default_target", "default_target"), ("user", "user"))


def render_rule(source: str, rule: dict) -> str:
    """Render one rule map (with an explicit ``source``) to a canonical policy line."""
    if not source:
        raise QubesProviderError(f"policy rule needs a source: {rule!r}")
    for key in ("service", "target", "action"):
        if not rule.get(key):
            raise QubesProviderError(f"policy rule needs {key!r}: {rule!r}")

    argument = rule.get("argument") or "*"
    line = "\t".join((rule["service"], argument, source, rule["target"], rule["action"]))
    for map_key, policy_key in _PARAMS:
        if rule.get(map_key):
            line += f" {policy_key}={rule[map_key]}"
    if rule.get("notify"):
        line += f" notify={_yesno(rule['notify'])}"
    return line


def render_file(rules: list, default_source: Optional[str]) -> str:
    """Render an ordered list of rule maps into a policy file body. ``default_source``
    is the implicit source for rules that omit ``source`` (the owning qube)."""
    lines = [_HEADER, ""]
    for rule in rules:
        lines.append(render_rule(rule.get("source") or default_source, rule))
    return "\n".join(lines) + "\n"


def validate(content: str) -> None:
    """Parse ``content`` to surface a friendly error before sending it to qubesd
    (which also validates and fails closed)."""
    try:
        StringPolicy(policy=content)
    except PolicySyntaxError as exc:
        raise QubesProviderError(f"invalid qrexec policy: {exc}") from exc


def _canonical(content: str) -> list:
    """A file's rules in qubesd's own canonical string form (whitespace- and
    default-normalized); used only to compare two files for equivalence."""
    return [str(rule) for rule in StringPolicy(policy=content).rules]


def parse_to_maps(content: str, *, include_source: bool) -> list:
    """Parse a policy file back into minimal rule maps (the inverse of
    :func:`render_file`), reading params from each rule's structured action so
    nothing is lost — e.g. ``deny notify=no``, which ``str(Rule)`` drops."""
    out = []
    for rule in StringPolicy(policy=content).rules:
        action = rule.action
        rmap = {"service": rule.service}
        if rule.argument:                       # falsy when the column is "*"
            rmap["argument"] = rule.argument
        if include_source:
            rmap["source"] = str(rule.source)
        rmap["target"] = str(rule.target)
        rmap["action"] = type(action).__name__.lower()   # Allow/Deny/Ask -> allow/...
        if getattr(action, "target", None):
            rmap["redirect"] = str(action.target)
        if getattr(action, "default_target", None):
            rmap["default_target"] = str(action.default_target)
        if getattr(action, "user", None):
            rmap["user"] = str(action.user)
        # notify defaults: deny=on, allow/ask=off — record only the non-default.
        notify = getattr(action, "notify", None)
        if isinstance(action, Deny):
            if notify is False:
                rmap["notify"] = "no"
        elif notify:
            rmap["notify"] = "yes"
        out.append(rmap)
    return out


def _yesno(value) -> str:
    return "no" if str(value).strip().lower() in ("no", "false", "0", "") else "yes"


class QubesPolicy:
    """Read/replace/remove whole policy files via the qrexec policy admin API.

    Provider-owned files are managed with the ``"any"`` token (no optimistic-
    concurrency check): they are namespaced to the provider, so each apply is the
    authority. Injectable (``client=``) for tests."""

    def __init__(self, client=None):
        self._client = client or PolicyClient()

    def names(self) -> list:
        return self._client.policy_list()

    def read(self, name: str) -> Optional[str]:
        """File content, or ``None`` if the file does not exist."""
        if name not in self.names():
            return None
        content, _token = self._client.policy_get(name)
        return content

    def replace(self, name: str, content: str) -> None:
        self._client.policy_replace(name, content, token="any")

    def remove(self, name: str) -> None:
        if name in self.names():
            self._client.policy_remove(name, token="any")


def write_text(backend: QubesPolicy, name: str, text: str) -> None:
    """Validate raw policy ``text`` and write it as the named file."""
    validate(text)
    backend.replace(name, text)


def write(backend: QubesPolicy, name: str, rules: list, default_source: Optional[str]) -> None:
    """Validate and write ``rules`` as the named policy file."""
    write_text(backend, name, render_file(rules, default_source))


def read_text_state(
    backend: QubesPolicy, name: str, declared: Optional[str]
) -> Optional[str]:
    """The raw policy text to put in state (``content`` mode), or ``None`` if the file
    is absent. Echo the declared text whenever the live file still canonically matches
    it (so comment/whitespace differences and qubesd token normalization don't show as
    drift); otherwise return the file verbatim (import / real out-of-band drift)."""
    content = backend.read(name)
    if content is None:
        return None
    if declared is not None and _canonical(content) == _canonical(declared):
        return declared
    return content


def read_state(
    backend: QubesPolicy,
    name: str,
    declared: Optional[list],
    default_source: Optional[str],
    *,
    include_source: bool,
) -> Optional[list]:
    """The rule maps to put in state, or ``None`` if the file is absent.

    When ``declared`` is given and the live file still canonically matches what we
    would render from it, the declared maps are echoed verbatim (no spurious diff —
    ``List(Map(String))`` has no semantic-equality hook, so post-apply state must
    equal config). Otherwise the file is re-derived from disk (import / real drift)."""
    content = backend.read(name)
    if content is None:
        return None
    if declared and _canonical(content) == _canonical(render_file(declared, default_source)):
        return declared
    return parse_to_maps(content, include_source=include_source)
