---
name: task-list-discipline
description: Pre-populated investigation tasks must be addressed in order; mark complete with evidence before moving on
version: 1.0.0
applies_to: ["*"]
---

# Task-list discipline

When a runbook matches an alert, the matcher pre-populates the
investigation's task list from the runbook's `prescribed_checks`.
Each task corresponds to one check in `checks.yaml`. The tasks must
be addressed in order. Each task that is marked `required: true` must
be marked `completed` --- with at least one supporting
evidence_ref --- before the investigation can pass the F8 quality
gate.

## The contract

The pre-populated task list is not advisory. It is the procedural
contract the runbook author has set for this class of incident. The
agent's job is to work the task list, not to guess at a shorter
investigation. Skipping ahead to a hypothesis without first
discharging the early-stage tasks risks reasoning over incomplete
evidence and biasing the conclusion toward the most readily-imagined
cause.

## How to mark a task complete

A task is `completed` when:

1. The agent executed at least one tool from the task's
   `suggested_tools` list (or a justified substitute from the
   runbook's `allowed_tools`).
2. The tool's output is recorded with a stable `evidence_object_id`.
3. The agent attaches the `evidence_object_id` to the task's
   `evidence_refs` field.
4. The agent writes a one-line summary of what the check found.

A task is `skipped` (not `completed`) when:

1. The check is `required: false` and the agent judges it not
   relevant to this incident, OR
2. The check cannot be executed because the required tool failed
   and a justified retry was attempted.

In both cases the agent must write a one-line `skip_rationale`
explaining the decision. Silent skips are procedural violations.

## Skipping required checks is a violation

A `required: true` check cannot be skipped. If the agent cannot
execute it, the investigation must fail with a structured
`procedural_violation` rather than proceed. The F8 quality gate
enforces this: an investigation that publishes Findings without
discharging every required check will not pass.

## Order matters

Run tasks in the order they appear in the list. Earlier tasks
establish facts that later tasks build on. If you find yourself
needing a later task's data to interpret an earlier task, that is a
signal that the runbook's task ordering may need an author's
attention --- file feedback rather than reordering on the fly.

## Optional checks deserve a decision

`required: false` checks are not safe to ignore. Mark them
`completed` if you ran them, `skipped` with a one-line rationale if
you chose not to. Either is acceptable; silently ignoring an
optional check is not. The skip_rationale is what the runbook owner
reads when reviewing whether the check should be promoted to
`required` or removed.
