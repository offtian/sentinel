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
3. If a plan in `docs/plans/` was completed, update its status and move it to the Complete section in `docs/plans/INDEX.md`

## Step 6: Check for stale content

Scan `docs/plans/INDEX.md` for any plan status that may be outdated based on recent changes. Flag these for the user.

## Rules

- NEVER update `docs/reviews/*` — these are frozen historical documents
- Status tracking lives ONLY in `docs/prd.md` acceptance criteria checkboxes
- Plan progress tracking lives in `docs/plans/INDEX.md`
- Be conservative: only suggest checking off criteria with clear evidence
