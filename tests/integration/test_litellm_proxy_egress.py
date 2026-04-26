"""
Egress acceptance test scaffold for the LiteLLM proxy migration (R-OB-1).

This test asserts that, with `LITELLM_BASE_URL` set and the network layer
blocking direct egress to provider endpoints (`api.openai.com`,
`api.anthropic.com`, `generativelanguage.googleapis.com`, …), an LLM call
issued by a Sentinel PydanticAI agent still succeeds because the call
egresses via the firm-shared proxy.

Setup recipe (CI / Helm work, deferred to wk5):

1. Stand up a LiteLLM proxy container reachable at `LITELLM_BASE_URL` and
   pre-loaded with a virtual key (`LITELLM_VIRTUAL_KEY`).
2. In the same docker network, install iptables rules (or a Kubernetes
   NetworkPolicy in the Helm chart) that DROP outbound traffic from the
   Sentinel container to the public provider endpoints listed above.
3. Run this test. Driving any agent factory's `.run(...)` should succeed
   end-to-end — the only outbound HTTP allowed is the request to the proxy.
4. Inverting the iptables rule (block the proxy too) should make this test
   fail closed; that variant lives in the Helm chart's smoke suite, not
   here.

The test is skip-marked at the foundations layer because the iptables /
NetworkPolicy enforcement is wk5 Helm work. The scaffold is committed now
so the intent is captured against the requirement and the test is ready
to flip on once the network policy lands.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason=(
        "Requires CI docker network with iptables egress block — wk5 Helm "
        "work (R-OB-1 full enforcement). See module docstring for the "
        "setup recipe."
    ),
)
def test_agent_call_succeeds_through_proxy_when_direct_egress_blocked():
    """
    With the proxy configured and direct egress to provider endpoints
    blocked at the network layer, an agent run must still succeed because
    the LLM call routes through the proxy.

    Skipped at the foundations layer — body is illustrative only.
    """
    # Given a configured LiteLLM proxy and iptables egress block on
    # api.openai.com / api.anthropic.com / generativelanguage.googleapis.com
    # (configured outside this process by the CI / Helm test harness)

    # When an alert classifier agent runs against a synthetic alert
    # from sentinel.interfaces.graphs.agents import alert_classifier
    # agent = alert_classifier.build_agent(model="openai/gpt-4.1-mini")
    # result = await agent.run("classify: CPU high on web-01")

    # Then the call completes successfully and outbound traffic is
    # observed only against ${LITELLM_BASE_URL}, never the provider hosts.
    pytest.fail("Scaffold body — flip on once R-OB-1 NetworkPolicy lands")
