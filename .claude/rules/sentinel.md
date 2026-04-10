---
paths:
  - "src/sentinel/**/*.py"
---

# Sentinel-Specific Conventions

## PydanticAI Agents

Agent definitions live in `interfaces/graphs/agents/`. Each agent module follows this pattern:

- Expose a `build_agent(*, model, skills)` factory function
- Dependencies as `@dataclasses.dataclass` (not attrs — PydanticAI requires mutable dataclass)
- Output types as Pydantic models or attrs frozen classes
- System prompts loaded from Jinja2 templates via `prompts.load_system_prompt()`
- Dynamic context via `@agent.instructions()` hook
- Skills injected via `@agent.system_prompt()` hook
- Always use `instrument=True` for OpenTelemetry tracing
- Placeholder model `"test"` — overridden at runtime via `.run(model=...)`

```python
# Pattern: agent factory
def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, OutputType]:
    system_prompt = utils.compose_system_prompt(
        base_prompt=BASE_SYSTEM_PROMPT, skill_names=skills
    )
    agent_instance = Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=OutputType,
        system_prompt=system_prompt,
        instrument=True,
    )
    return agent_instance
```

## Pydantic Graph Pipelines

Pipeline definitions live in `interfaces/graphs/`. Each pipeline follows this pattern:

- Nodes extend `BaseNode[State, Dependencies, OutputType]`
- Node logic wrapped in inner `async def _impl()` function
- All node runs instrumented via `instrumented_node_run()` wrapper
- Exception handling emits structured logs — never silent catch
- Graph defined as `Graph(nodes=(Node1, Node2, ...))`

```python
# Pattern: instrumented node
async def run(self, ctx: GraphRunContext[State, Dependencies]) -> NextNode | End[Output]:
    async def _impl() -> NextNode | End[Output]:
        # Node logic here
        ...
    return await instrumented_node_run(
        pipeline="sre", node="classify_alert", fn=_impl
    )()
```

## Vendor Adapters

External service clients in `domain/vendor_adapters/` follow the no-op pattern:

- `is_configured` property checks if credentials exist
- Methods return `None` or gracefully degrade when unconfigured
- Always log skips (`"service_action_skipped"`) and failures
- Never raise on missing configuration — the platform must be runnable with partial vendor setup

## Configuration

- `settings.py` — env vars via pydantic-settings (`get_settings()` singleton)
- `config.py` — wires adapters, searchers, toolsets, agents (`get_config()` singleton)
- Model names use `provider/model` format (e.g., `"openai/gpt-4.1"`), normalized to `provider:model` for pydantic-ai
- Skills mapping declared in `SKILLS_BY_AGENT` dict in config

## Data Layer (SQLModel)

- Models are thin — no business logic
- Timestamps always UTC with timezone info
- JSON data stored in JSONB columns
- Indexes on frequently-queried fields
- `__tablename__` explicitly set on every model

## Prompt Templates

- Jinja2 templates in `domain/prompts/` (`.j2` files)
- Two blocks: `system` (static instructions) and `user` (dynamic context)
- Loaded via `load_system_prompt()` / `render_user_prompt()`

## Skills System

- Skills are directories in `domain/skills/` with `SKILL.md` (YAML frontmatter + markdown body)
- `applies_to` field uses fnmatch patterns for category matching
- Static skills baked at agent build time, dynamic skills injected at runtime
