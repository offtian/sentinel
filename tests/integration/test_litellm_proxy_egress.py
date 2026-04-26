from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason=(
        "R-OB-1 full enforcement — requires CI docker network with iptables "
        "egress block (wk5 Helm work). Setup recipe in the F5 plan."
    ),
)
def test_agent_call_succeeds_through_proxy_when_direct_egress_blocked():
    # Given a configured LiteLLM proxy and a NetworkPolicy blocking direct
    # egress to provider endpoints (api.openai.com, api.anthropic.com, …)

    # When an agent runs against a synthetic input

    # Then the call completes via the proxy and no provider host is contacted
    pytest.fail("Scaffold body — flip on once R-OB-1 NetworkPolicy lands")
