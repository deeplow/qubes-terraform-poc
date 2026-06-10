# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``qubes_prefs`` resource: manage dom0 global preferences (the ``qubes-prefs``
CLI surface) declaratively.

Unlike ``qubes_vm`` (which delegates to qubes-ansible), this talks to the Qubes
**Admin API** directly via ``qubesadmin`` — the dom0 globals are a ``PropertyHolder``
on ``qubesadmin.Qubes()`` (``admin.property.*``). It is a singleton: there is one
dom0 globals object. Each global property is a typed ``optional`` (not computed)
attribute, enumerated live from ``property_list()``; setting one enforces it and
**removing it resets it to the Qubes default** (``destroy`` resets all managed ones).
"""

from typing import Optional

import qubesadmin

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

# Singleton identity (the dom0 globals object).
_SINGLETON_ID = "dom0"


def _is_set(value) -> bool:
    """True if a config/state value is a real value (not null / not Unknown)."""
    return value is not None and value is not Unknown


class QubesPrefs:
    """Read/enforce/reset dom0 global properties straight from qubesadmin.

    Injectable into the resource (``backend=``) for tests."""

    def _app(self):
        return qubesadmin.Qubes()

    def names(self) -> list:                                # admin.property.List (dom0)
        return sorted(self._app().property_list())

    def enforce(self, desired: dict) -> None:               # admin.property.Set, idempotent
        app = self._app()
        for prop, value in desired.items():
            if self._norm(getattr(app, prop)) != value:
                setattr(app, prop, value)

    def reset(self, props) -> None:                         # admin.property.Reset (removal / destroy)
        app = self._app()
        for prop in props:
            try:
                if not app.property_is_default(prop):
                    setattr(app, prop, qubesadmin.DEFAULT)
            except Exception:  # noqa: BLE001 - best-effort reset
                pass

    def project(self, props) -> dict:                       # declared props -> live value
        app = self._app()
        return {p: self._norm(getattr(app, p)) for p in props}

    def customized(self) -> dict:                           # import: adopt non-default globals
        app = self._app()
        return {p: self._norm(getattr(app, p))
                for p in app.property_list() if not app.property_is_default(p)}

    @staticmethod
    def _norm(value) -> str:
        value = getattr(value, "name", value)               # VM-valued props -> name
        return "" if value is None else str(value)


class QubesPrefsResource(Resource):
    """The dom0 global-preferences singleton, managed via the Admin API.

      create/update -> QubesPrefs.enforce (+ reset de-declared on update)
      read          -> QubesPrefs.project (declared subset; rest null)
      delete        -> QubesPrefs.reset (all managed -> default)
    """

    def __init__(self, provider, backend=None):
        self.provider = provider
        self.qubes = backend or QubesPrefs()  # injectable for tests

    @classmethod
    def get_name(cls) -> str:
        # Combined with the provider model prefix "qubes_" -> "qubes_prefs".
        return "prefs"

    @classmethod
    def get_schema(cls) -> schema.Schema:
        # One optional (non-computed) String per dom0 global, enumerated live from
        # qubesadmin (admin.property.List) — needs a reachable qubesd at schema time.
        attributes = [
            schema.Attribute(name, types.String(), optional=True)
            for name in QubesPrefs().names()
        ]
        attributes.append(
            schema.Attribute("id", types.String(), computed=True,
                             description="Singleton id (always 'dom0').")
        )
        return schema.Schema(version=1, attributes=attributes)

    # --- helpers ------------------------------------------------------------

    def _declared(self, data: dict) -> dict:
        """The managed subset: declared (set) global properties from a config/state dict."""
        return {p: data[p] for p in self.qubes.names() if _is_set(data.get(p))}

    def _state(self, declared: dict) -> dict:
        """Full state: declared props projected to their live value, every other
        global null (non-computed -> undeclared must be null), plus the singleton id."""
        state = {p: None for p in self.qubes.names()}
        state.update(self.qubes.project(declared))
        state["id"] = _SINGLETON_ID
        return state

    # --- CRUD ---------------------------------------------------------------

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        try:
            declared = self._declared(planned)
            self.qubes.enforce(declared)
            return self._state(declared)
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to set Qubes preferences", str(exc))
            return None

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
        try:
            return self._state(self._declared(current))
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to read Qubes preferences", str(exc))
            return None

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> Optional[dict]:
        try:
            declared = self._declared(planned)
            self.qubes.enforce(declared)
            # Properties dropped from config are reset to their Qubes default.
            self.qubes.reset(set(self._declared(current)) - set(declared))
            return self._state(declared)
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to update Qubes preferences", str(exc))
            return None

    def delete(self, ctx: DeleteContext, current: dict):
        try:
            self.qubes.reset(self._declared(current).keys())
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to reset Qubes preferences", str(exc))

    def import_(self, ctx: ImportContext, id: str) -> Optional[dict]:
        """Adopt the currently non-default dom0 globals into state."""
        try:
            return self._state(self.qubes.customized())
        except Exception as exc:  # noqa: BLE001
            ctx.diagnostics.add_error("Failed to import Qubes preferences", str(exc))
            return None
