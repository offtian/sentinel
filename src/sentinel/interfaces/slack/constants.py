from __future__ import annotations


# Slack action_id values (must match Block Kit button action_id in _blocks.py)
ACTION_APPROVE_INVESTIGATION = "approve_investigation"
ACTION_REJECT_INVESTIGATION = "reject_investigation"

# Structured log event names for Slack operations
EVENT_REQUEST_RECEIVED = "slack.request_received"
EVENT_REQUEST_ERROR = "slack.request_error"
EVENT_SRE_COMPLETE = "slack.sre_investigation_complete"
EVENT_SUPPORT_COMPLETE = "slack.support_review_complete"
EVENT_INTENT_CLASSIFIED = "slack.intent_classified"
