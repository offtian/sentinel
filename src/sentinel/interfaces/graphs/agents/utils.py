from __future__ import annotations


def get_model_with_gateway(model_name: str) -> str:
    """
    Normalise model names for pydantic-ai and route via LiteLLM.

    - Config uses \"openai/gpt-4.1-mini\" style names.
    - pydantic-ai expects \"openai:gpt-4.1-mini\".
    - LiteLLM runs as an OpenAI-compatible proxy on OPENAI_BASE_URL,
      so we don't need a custom provider name pydantic-ai's built-in
      OpenAI provider will hit the proxy.
    """
    # Strip any legacy litellm_proxy/ prefix
    model_name = model_name.removeprefix("litellm_proxy/")

    # Convert \"provider/model\" → \"provider:model\" if needed
    if "/" in model_name:
        provider, name = model_name.split("/", 1)
        model_name = f"{provider}:{name}"

    return model_name
