import { describe, expect, it } from "vitest";
import {
  buildUpdateBody,
  EMPTY_AI_SETTINGS_FORM,
  formFromPayload,
  isLegacySource,
  probeSummary,
  providerLabel,
  sourceLabel,
  type AISettingsPayload,
} from "./aiSettings";

const PAYLOAD: AISettingsPayload = {
  chat: {
    provider: "openai_compat",
    base_url: "https://gateway.example/v1",
    model: "LongCat-2.0",
    timeout: 600,
    max_tokens: null,
    reasoning_effort: null,
    proxy: "http://127.0.0.1:7890",
    num_ctx: 65536,
    api_key_set: true,
    api_key_masked: "********1234",
  },
  embedding: {
    provider: "ollama",
    base_url: "http://127.0.0.1:11434",
    model: "qwen3-embedding:0.6b",
    timeout: 120,
    dimensions: 1024,
  },
  sources: { model: "OPENAI_COMPAT_MODEL", timeout: "default" },
  // No assertion reads this field; it is a fixture value only.  Kept relative so
  // the suite does not advertise the author's machine path in a public repo.
  env_file: ".env",
  env_file_exists: true,
  managed_keys: ["AI_PROVIDER"],
  legacy_keys: ["OPENAI_COMPAT_MODEL"],
  supported_chat_providers: ["openai_compat", "ollama"],
  supported_embedding_providers: ["openai_compat", "ollama"],
};

describe("formFromPayload", () => {
  it("prefills every effective value", () => {
    const form = formFromPayload(PAYLOAD);
    expect(form.provider).toBe("openai_compat");
    expect(form.baseUrl).toBe("https://gateway.example/v1");
    expect(form.timeout).toBe("600");
    expect(form.numCtx).toBe("65536");
    expect(form.embedModel).toBe("qwen3-embedding:0.6b");
    expect(form.embedDimensions).toBe("1024");
  });

  it("never prefills the api key from the masked value", () => {
    expect(formFromPayload(PAYLOAD).apiKey).toBe("");
  });

  it("renders null optionals as empty strings", () => {
    const form = formFromPayload(PAYLOAD);
    expect(form.maxTokens).toBe("");
    expect(form.reasoningEffort).toBe("");
  });
});

describe("buildUpdateBody", () => {
  it("sends the edited values, coercing numbers", () => {
    const form = formFromPayload(PAYLOAD);
    form.model = "  new-model  ";
    form.timeout = "45";
    const body = buildUpdateBody(form);
    expect(body.model).toBe("new-model");
    expect(body.timeout).toBe(45);
    expect(body.embed_dimensions).toBe(1024);
  });

  it("omits the api key when the field was left untouched", () => {
    const body = buildUpdateBody(formFromPayload(PAYLOAD));
    expect("api_key" in body).toBe(false);
  });

  it("sends the api key only when one was typed", () => {
    const form = formFromPayload(PAYLOAD);
    form.apiKey = "sk-new-key";
    expect(buildUpdateBody(form).api_key).toBe("sk-new-key");
  });

  it("clears the api key on an explicit request even with an empty field", () => {
    const body = buildUpdateBody(formFromPayload(PAYLOAD), { clearApiKey: true });
    expect(body.api_key).toBe("");
  });

  it("treats an emptied field as an explicit clear", () => {
    const form = formFromPayload(PAYLOAD);
    form.model = "";
    form.proxy = "";
    form.timeout = "";
    const body = buildUpdateBody(form);
    expect(body.model).toBe("");
    expect(body.proxy).toBe("");
    expect(body.timeout).toBe("");
  });

  it("never sends NaN for a non-numeric entry", () => {
    const form = formFromPayload(PAYLOAD);
    form.numCtx = "not-a-number";
    expect(buildUpdateBody(form).num_ctx).toBe("");
  });

  it("returns a body for the empty form without throwing", () => {
    const body = buildUpdateBody({ ...EMPTY_AI_SETTINGS_FORM });
    expect(body.provider).toBe("openai_compat");
    expect(body.timeout).toBe("");
  });
});

describe("source labels", () => {
  it("names the built-in default", () => {
    expect(sourceLabel("default")).toBe("内置默认");
  });

  it("names the variable that supplied the value", () => {
    expect(sourceLabel("AI_MODEL")).toBe("AI_MODEL");
    expect(sourceLabel("OPENAI_COMPAT_MODEL")).toBe("OPENAI_COMPAT_MODEL");
  });

  it("flags legacy variables so the operator knows where a value came from", () => {
    expect(isLegacySource("OPENAI_COMPAT_MODEL")).toBe(true);
    expect(isLegacySource("OLLAMA_URL")).toBe(true);
    expect(isLegacySource("AI_MODEL")).toBe(false);
    expect(isLegacySource("default")).toBe(false);
  });
});

describe("probeSummary", () => {
  it("reports a failure with its reason", () => {
    const summary = probeSummary({
      ok: false,
      provider: "openai_compat",
      model: "m",
      base_url: "u",
      detail: "channel down",
      latency_ms: null,
      embedding_ok: null,
      embedding_detail: null,
      embedding_model: null,
    });
    expect(summary).toContain("连接失败");
    expect(summary).toContain("channel down");
  });

  it("reports success with latency and embedding state", () => {
    const summary = probeSummary({
      ok: true,
      provider: "ollama",
      model: "qwen3",
      base_url: "u",
      detail: "ok",
      latency_ms: 42,
      embedding_ok: true,
      embedding_detail: null,
      embedding_model: "qwen3-embedding:0.6b",
    });
    expect(summary).toContain("连接正常");
    expect(summary).toContain("42 ms");
    expect(summary).toContain("向量通道正常");
  });

  it("reports a failed embedding channel without hiding the chat success", () => {
    const summary = probeSummary({
      ok: true,
      provider: "ollama",
      model: "qwen3",
      base_url: "u",
      detail: "ok",
      latency_ms: 42,
      embedding_ok: false,
      embedding_detail: "offline",
      embedding_model: "m",
    });
    expect(summary).toContain("向量通道失败");
    expect(summary).toContain("offline");
  });
});

describe("providerLabel", () => {
  it("renders known providers in the operator's language", () => {
    expect(providerLabel("ollama")).toBe("本地 Ollama");
    expect(providerLabel("openai_compat")).toBe("OpenAI 兼容云端");
    expect(providerLabel("mystery")).toBe("mystery");
  });
});
