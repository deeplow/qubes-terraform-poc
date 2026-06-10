# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures for the provider tests."""

import pytest

from tests.fakes import FakeApp, install_qube_fakes


@pytest.fixture
def fake_app():
    """A fresh in-memory Qubes (with fedora-40 template + sys-firewall)."""
    return FakeApp()


@pytest.fixture
def qube_env(monkeypatch, fake_app):
    """Wire the bundled qubes-ansible QubeModule/qube_facts onto ``fake_app``.

    Yields the FakeApp so tests can assert on resulting domain state."""
    install_qube_fakes(monkeypatch, fake_app)
    return fake_app
