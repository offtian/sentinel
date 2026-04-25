from __future__ import annotations

import pytest

from tests.factories import make_alert, make_ticket


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
