"""
Shared configuration substrate for every team profile.

Hosts ``CommonConfiguration`` — the concrete ``BaseConfiguration``
subclass that wires vendor adapters, searchers, toolsets, and agents.
Future shared asset trees (runbooks, skills, tools).

Policy primitives (``ApprovalPolicy``, ``OutputChannel``,
``RedactionPolicy``) live in ``sentinel.data.policies``.
"""
