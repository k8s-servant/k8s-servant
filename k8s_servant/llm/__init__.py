"""The chat model client: neutral types plus one module per wire API.

Callers import the neutral types from `k8s_servant.llm.types` and the factory
and error helpers from here; which API client is behind `build_chat` is a detail
of this package.
"""

from k8s_servant.llm.chat_completions import (
    ChatCompletionsChat,
    build_chat,
    build_client,
    explain_model_error,
    normalize_endpoint,
)

__all__ = ["ChatCompletionsChat", "build_chat", "build_client", "explain_model_error", "normalize_endpoint"]
