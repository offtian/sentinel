from sentinel.interfaces.graphs.agents import intent_router


class TestIntentRouterAgent:
    def test_base_system_prompt_loaded(self):
        # Given the base system prompt constant
        # Then it contains expected content
        assert intent_router.BASE_SYSTEM_PROMPT
        assert "SRE Investigation" in intent_router.BASE_SYSTEM_PROMPT
        assert "Support Review" in intent_router.BASE_SYSTEM_PROMPT

    def test_build_agent_output_type_is_intent_classification(self):
        # Given an agent built from the factory
        agent = intent_router.build_agent()

        # Then its output type is IntentClassification
        assert agent.output_type is intent_router.IntentClassification

    def test_intent_enum_values(self):
        assert intent_router.Intent.SRE.value == "sre"
        assert intent_router.Intent.SUPPORT.value == "support"

    def test_intent_classification_model(self):
        classification = intent_router.IntentClassification(
            intent=intent_router.Intent.SRE,
            rationale="Message describes a production outage",
        )
        assert classification.intent == intent_router.Intent.SRE
        assert "outage" in classification.rationale

    def test_dependencies_hold_message(self):
        deps = intent_router.Dependencies(message="pods are crashing")
        assert deps.message == "pods are crashing"
