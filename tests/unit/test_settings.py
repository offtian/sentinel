from __future__ import annotations

from sentinel import settings


class TestK8sChartSettings:
    def test_defaults_are_set(self):
        # Given default settings
        s = settings.Settings()

        # Then chart agent settings have expected defaults
        assert s.k8s_chart_generator_llm == "openai/gpt-4.1"
        assert s.k8s_chart_parser_llm == "openai/gpt-4.1-mini"
        assert s.k8s_chart_auto_validate is False
        assert s.k8s_chart_auto_sandbox is False
        assert s.k8s_chart_sandbox_context == ""
        assert s.k8s_chart_max_retries == 3
