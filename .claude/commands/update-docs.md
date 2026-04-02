You are updating project documentation after recent work. Follow these steps exactly:

## Step 1: Identify recent changes

Run `git log --oneline -20` and `git diff HEAD~5 --stat` to understand what was recently implemented.

## Step 2: Read the PRD acceptance criteria

Read `docs/prd.md` and identify all unchecked acceptance criteria (`- [ ]`).

## Step 3: Match changes to criteria

For each unchecked criterion, determine if recent commits resolve it. Consider:
- Does a new file or test cover the criterion?
- Does a commit message reference the feature?
- Does a code change implement the described behavior?

## Step 4: Present suggestions

Present a table of suggested updates:

| Criterion | File/Line | Evidence | Suggested Action |
|-----------|-----------|----------|-----------------|

Wait for user confirmation before making any changes.

## Step 5: Apply approved updates

For each approved suggestion:
1. Check off the criterion in `docs/prd.md` (change `- [ ]` to `- [x]`)
2. If the change affects architecture (new layers, new patterns, new integrations), update `docs/architecture.md`
3. If `docs/claude-plan.md` references the topic, verify the operational context is still accurate

## Step 6: Check for stale content

Scan `docs/claude-plan.md` for any references that may be outdated based on recent changes (renamed files, moved modules, changed patterns). Flag these for the user.

## Rules

- NEVER update `SENTINEL_ARCHITECTURE_REVIEW.md` — it is a frozen historical document
- Status tracking lives ONLY in `docs/prd.md` acceptance criteria checkboxes
- `docs/claude-plan.md` contains operational context, not status — only update if facts changed
- Be conservative: only suggest checking off criteria with clear evidence
