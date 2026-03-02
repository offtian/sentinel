from __future__ import annotations

from environs import Env


env = Env()
env.read_env()

# Environment
ENVIRONMENT: str = env.str("ENVIRONMENT", default="production")
DATABASE_URL: str = env.str("DATABASE_URL", default="")

# LLM models (via LiteLLM gateway)
AI_GATEWAY_URL: str = env.str("AI_GATEWAY_URL", default="http://litellm.litellm.svc.cluster.local/")
ALERT_CLASSIFIER_LLM: str = env.str("ALERT_CLASSIFIER_LLM", default="openai/gpt-4.1-mini")
ROOT_CAUSE_LLM: str = env.str("ROOT_CAUSE_LLM", default="openai/gpt-4.1")
TICKET_REVIEWER_LLM: str = env.str("TICKET_REVIEWER_LLM", default="openai/gpt-4.1-mini")
RESPONSE_DRAFTER_LLM: str = env.str("RESPONSE_DRAFTER_LLM", default="openai/gpt-4.1")

# SRE config
PAGERDUTY_API_KEY: str = env.str("PAGERDUTY_API_KEY", default="")
DATADOG_API_KEY: str = env.str("DATADOG_API_KEY", default="")
DATADOG_APP_KEY: str = env.str("DATADOG_APP_KEY", default="")
HOLMESGPT_ENABLED: bool = env.bool("HOLMESGPT_ENABLED", default=True)

# Support config
JIRA_BASE_URL: str = env.str("JIRA_BASE_URL", default="")
JIRA_API_TOKEN: str = env.str("JIRA_API_TOKEN", default="")
JIRA_USER_EMAIL: str = env.str("JIRA_USER_EMAIL", default="")
CONFLUENCE_BASE_URL: str = env.str("CONFLUENCE_BASE_URL", default="")

# Shared search
DOCUMENT_SEARCHER: str = env.str("DOCUMENT_SEARCHER", default="bedrock_knowledge_base")

# Feature flags
SRE_AUTO_INVESTIGATE: bool = env.bool("SRE_AUTO_INVESTIGATE", default=True)
SUPPORT_AUTO_DRAFT: bool = env.bool("SUPPORT_AUTO_DRAFT", default=True)

# Slack (for posting findings)
SLACK_BOT_TOKEN: str = env.str("SLACK_BOT_TOKEN", default="")
SRE_SLACK_CHANNEL: str = env.str("SRE_SLACK_CHANNEL", default="")
SUPPORT_SLACK_CHANNEL: str = env.str("SUPPORT_SLACK_CHANNEL", default="")

# Observability
DD_SERVICE: str = "sentinel"
DD_ENV: str = env.str("DD_ENV", default="production")
