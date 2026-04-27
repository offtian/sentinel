"""
Sentinel runbook catalog (F6).

A runbook is a four-file directory under
``src/sentinel/plugins/{common,teams/<team>}/runbooks/<runbook_id>/``:

* ``RUNBOOK.md`` — Markdown body + YAML frontmatter (the contract)
* ``tools.yaml`` — capability scope (allowed tools + max_calls)
* ``checks.yaml`` — prescribed procedure → ``investigation_task`` list
* ``tests.yaml`` — golden fixtures (alert → expected match + behaviour)

:mod:`models` defines the frozen data shapes; :mod:`loader` reads
directories from disk; :mod:`matcher` runs the three-stage match pipeline
(deterministic tag pre-filter → LLM tie-disambiguator → LLM zero-match
rescue). The disambiguator agent itself lives at
:mod:`sentinel.interfaces.graphs.agents.runbook_disambiguator`.

See ``docs/superpowers/specs/2026-04-26-f6-runbook-catalog-design.md`` for
the full design.
"""

from __future__ import annotations
