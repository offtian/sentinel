from __future__ import annotations

from sentinel import _config


def get_model_with_gateway(model_name: str) -> str:
    """
    Prefix a model name with the LiteLLM proxy prefix so that PydanticAI
    routes the request through our gateway.
    """
    if model_name.startswith("litellm_proxy/"):
        return model_name
    return f"litellm_proxy/{model_name}"


def get_gateway_base_url() -> str:
    return _config.AI_GATEWAY_URL
