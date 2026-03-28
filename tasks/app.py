from __future__ import annotations

import invoke


@invoke.task
def run_api(ctx: invoke.Context) -> None:
    """
    Start the FastAPI development server with live reload.
    """
    ctx.run(
        "uv run uvicorn sentinel.interfaces.api.app:app "
        "--host 0.0.0.0 --port 8000 --reload"
    )


@invoke.task
def smoke_test(ctx: invoke.Context) -> None:
    """
    Hit the /health endpoint to verify the API is responding.
    """
    ctx.run("curl -s http://localhost:8000/health | python -m json.tool")
