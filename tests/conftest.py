from __future__ import annotations

import pytest

from sentinel import settings as settings_mod
from tests.factories import make_alert, make_ticket


@pytest.fixture
def fake_settings() -> settings_mod.Settings:
    """
    Test stand-in for the global Settings singleton.

    Built via ``Settings.model_construct`` so it skips Pydantic validation —
    tests can mutate any field freely without tripping HttpUrl / SecretStr
    constraints.
    """
    return settings_mod.Settings.model_construct()


@pytest.fixture
def patch_settings(fake_settings, monkeypatch):
    """
    Patch the Settings singleton into one or more consumer modules.

    Use when a test exercises code that imported ``from sentinel.settings
    import settings``. Pass each consumer module that holds a reference to
    the singleton; this fixture rebinds the name to ``fake_settings`` for
    the duration of the test.

    Returns the ``fake_settings`` instance so the test can mutate fields::

        def test_foo(patch_settings):
            fake = patch_settings(slack_module, k8s_module)
            fake.slack_bot_token = "xoxb-test"
            # exercise
    """

    def _patch(*modules: object) -> settings_mod.Settings:
        for module in modules:
            monkeypatch.setattr(module, "settings", fake_settings)
        return fake_settings

    return _patch


@pytest.fixture
def sample_alert():
    return make_alert()


@pytest.fixture
def critical_alert():
    from sentinel.domain.alerts import entities as alert_entities

    return make_alert(
        alert_id="P999CRIT",
        title="Database completely unreachable",
        description="All DB connections failing, 100% error rate",
        severity=alert_entities.AlertSeverity.CRITICAL,
        service="db-primary",
    )


@pytest.fixture
def sample_ticket():
    return make_ticket()
