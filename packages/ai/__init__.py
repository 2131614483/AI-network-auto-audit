"""Unified AI access layer — one gateway, one configuration surface.

Every model call in this project (canvas/planning chat, canvas streaming chat,
knowledge embeddings, future additions) resolves its provider through
:mod:`packages.ai.config` and travels over :class:`~packages.ai.gateway.UnifiedAIClient`.
The provider, endpoint, model, key, timeout and embedding settings live in the
project ``.env``, are editable from the desktop AI settings page, and take
effect on the next call without a restart.

The layer is transport only.  It grants no execution authority: a plan drafted
through it still passes the same capability-recall, data-boundary and compiler
gates before anything can run.
"""

from .config import (
    DEFAULT_CHAT_PROVIDER,
    DEFAULT_EMBED_PROVIDER,
    LEGACY_ENV,
    MANAGED_CHAT_ENV,
    MANAGED_EMBED_ENV,
    MANAGED_ENV,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPAT,
    SUPPORTED_CHAT_PROVIDERS,
    SUPPORTED_EMBED_PROVIDERS,
    ChatProviderConfig,
    EmbeddingProviderConfig,
    describe_chat_config,
    describe_embedding_config,
    mask_secret,
    resolve_chat_config,
    resolve_chat_provider,
    resolve_embedding_config,
)
from .embedding import Embedder, UnifiedEmbedder, default_embedder
from .env_store import (
    EnvWriteError,
    apply_to_environ,
    env_file_path,
    load_env_file,
    load_env_file_once,
    read_env_file,
    skip_dotenv,
    update_env_file,
)
from .errors import AIClientError, AIConfigurationError
from .gateway import AIProbeResult, UnifiedAIClient, get_chat_client, probe

__all__ = [
    "DEFAULT_CHAT_PROVIDER",
    "DEFAULT_EMBED_PROVIDER",
    "LEGACY_ENV",
    "MANAGED_CHAT_ENV",
    "MANAGED_EMBED_ENV",
    "MANAGED_ENV",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI_COMPAT",
    "SUPPORTED_CHAT_PROVIDERS",
    "SUPPORTED_EMBED_PROVIDERS",
    "AIClientError",
    "AIConfigurationError",
    "AIProbeResult",
    "ChatProviderConfig",
    "Embedder",
    "EmbeddingProviderConfig",
    "EnvWriteError",
    "UnifiedAIClient",
    "UnifiedEmbedder",
    "apply_to_environ",
    "default_embedder",
    "describe_chat_config",
    "describe_embedding_config",
    "env_file_path",
    "get_chat_client",
    "load_env_file",
    "load_env_file_once",
    "mask_secret",
    "probe",
    "read_env_file",
    "resolve_chat_config",
    "resolve_chat_provider",
    "resolve_embedding_config",
    "skip_dotenv",
    "update_env_file",
]
