/**
 * Pure helpers for the AI settings page.
 *
 * The page edits one "effective configuration" that may come from three
 * different places (unified `AI_*` variables, the legacy `OPENAI_COMPAT_*` /
 * `OLLAMA_*` names, or built-in defaults).  Keeping the mapping in pure
 * functions means the form/API contract is testable without Electron, and the
 * component stays a thin renderer.
 */

export type ChatSettings = {
  provider: string;
  base_url: string;
  model: string;
  timeout: number;
  max_tokens: number | null;
  reasoning_effort: string | null;
  proxy: string | null;
  num_ctx: number;
  api_key_set: boolean;
  api_key_masked: string | null;
};

export type EmbeddingSettings = {
  provider: string;
  base_url: string;
  model: string;
  timeout: number;
  dimensions: number;
};

export type AISettingsPayload = {
  chat: ChatSettings;
  embedding: EmbeddingSettings;
  sources: Record<string, string>;
  env_file: string;
  env_file_exists: boolean;
  managed_keys: string[];
  legacy_keys: string[];
  supported_chat_providers: string[];
  supported_embedding_providers: string[];
};

export type AIProbeResult = {
  ok: boolean;
  provider: string;
  model: string;
  base_url: string;
  detail: string;
  latency_ms: number | null;
  embedding_ok: boolean | null;
  embedding_detail: string | null;
  embedding_model: string | null;
};

/**
 * Editable form state.  Everything is a string so "cleared" (empty) is
 * distinguishable from "unchanged" and from a valid number.
 */
export type AISettingsForm = {
  provider: string;
  baseUrl: string;
  model: string;
  timeout: string;
  maxTokens: string;
  reasoningEffort: string;
  proxy: string;
  numCtx: string;
  embedProvider: string;
  embedBaseUrl: string;
  embedModel: string;
  embedTimeout: string;
  embedDimensions: string;
  /** Write-only: never prefilled from the server (which only sends a mask). */
  apiKey: string;
};

export const EMPTY_AI_SETTINGS_FORM: AISettingsForm = {
  provider: "openai_compat",
  baseUrl: "",
  model: "",
  timeout: "",
  maxTokens: "",
  reasoningEffort: "",
  proxy: "",
  numCtx: "",
  embedProvider: "ollama",
  embedBaseUrl: "",
  embedModel: "",
  embedTimeout: "",
  embedDimensions: "",
  apiKey: "",
};

function text(value: string | number | null): string {
  return value === null || value === undefined ? "" : String(value);
}

/** Prefill the form from the effective configuration returned by the API. */
export function formFromPayload(payload: AISettingsPayload): AISettingsForm {
  const { chat, embedding } = payload;
  return {
    provider: chat.provider,
    baseUrl: chat.base_url,
    model: chat.model,
    timeout: text(chat.timeout),
    maxTokens: text(chat.max_tokens),
    reasoningEffort: text(chat.reasoning_effort),
    proxy: text(chat.proxy),
    numCtx: text(chat.num_ctx),
    embedProvider: embedding.provider,
    embedBaseUrl: embedding.base_url,
    embedModel: embedding.model,
    embedTimeout: text(embedding.timeout),
    embedDimensions: text(embedding.dimensions),
    apiKey: "",
  };
}

/**
 * An empty field means "clear this setting": the gateway then falls back to the
 * legacy variable and finally to the built-in default.  A non-numeric value is
 * treated the same way rather than being sent as `NaN`.
 */
function numeric(value: string): number | "" {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : "";
}

function trimmed(value: string): string {
  return value.trim();
}

/**
 * Build the partial update body.
 *
 * ``api_key`` is only sent when the operator actually typed one: an empty field
 * means "leave the stored key alone", and clearing it is an explicit action
 * (``clearApiKey``).  That asymmetry is deliberate — silently wiping a working
 * credential because a form was saved would be a nasty surprise.
 */
export function buildUpdateBody(
  form: AISettingsForm,
  options: { clearApiKey?: boolean } = {},
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    provider: trimmed(form.provider),
    base_url: trimmed(form.baseUrl),
    model: trimmed(form.model),
    timeout: numeric(form.timeout),
    max_tokens: numeric(form.maxTokens),
    reasoning_effort: trimmed(form.reasoningEffort),
    proxy: trimmed(form.proxy),
    num_ctx: numeric(form.numCtx),
    embed_provider: trimmed(form.embedProvider),
    embed_base_url: trimmed(form.embedBaseUrl),
    embed_model: trimmed(form.embedModel),
    embed_timeout: numeric(form.embedTimeout),
    embed_dimensions: numeric(form.embedDimensions),
  };
  if (options.clearApiKey) {
    body.api_key = "";
  } else if (trimmed(form.apiKey)) {
    body.api_key = trimmed(form.apiKey);
  }
  return body;
}

const SOURCE_LABELS: Record<string, string> = {
  default: "内置默认",
  AI_PROVIDER: "AI_PROVIDER",
  AI_BASE_URL: "AI_BASE_URL",
  AI_MODEL: "AI_MODEL",
  AI_API_KEY: "AI_API_KEY",
  AI_TIMEOUT: "AI_TIMEOUT",
  AI_NUM_CTX: "AI_NUM_CTX",
  AI_EMBED_PROVIDER: "AI_EMBED_PROVIDER",
  AI_EMBED_BASE_URL: "AI_EMBED_BASE_URL",
  AI_EMBED_MODEL: "AI_EMBED_MODEL",
  AI_EMBED_TIMEOUT: "AI_EMBED_TIMEOUT",
  AI_EMBED_DIMENSIONS: "AI_EMBED_DIMENSIONS",
};

/**
 * Where a value came from, for the operator's benefit: a field showing a value
 * they never typed in this page is confusing unless the source is visible.
 */
export function sourceLabel(source: string | undefined): string {
  if (!source) return "未知";
  if (source === "default") return SOURCE_LABELS.default;
  return SOURCE_LABELS[source] ?? source;
}

/** True when the value comes from a legacy variable rather than this page. */
export function isLegacySource(source: string | undefined): boolean {
  if (!source) return false;
  return source !== "default" && !source.startsWith("AI_");
}

export function providerLabel(provider: string): string {
  if (provider === "ollama") return "本地 Ollama";
  if (provider === "openai_compat") return "OpenAI 兼容云端";
  return provider;
}

/** Render a probe outcome as one human sentence. */
export function probeSummary(result: AIProbeResult): string {
  if (!result.ok) return `连接失败：${result.detail}`;
  const latency = result.latency_ms === null ? "" : `，${result.latency_ms} ms`;
  const embedding =
    result.embedding_ok === null
      ? ""
      : result.embedding_ok
        ? `；向量通道正常（${result.embedding_model ?? "未知模型"}）`
        : `；向量通道失败：${result.embedding_detail ?? "未知原因"}`;
  return `连接正常（${providerLabel(result.provider)} · ${result.model}${latency}）${embedding}`;
}
