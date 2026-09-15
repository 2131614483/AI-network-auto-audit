import { describe, expect, it } from "vitest";
import {
  formatLogTimestamp,
  inferLogLevel,
  inferLogSource,
  logLevelLabel,
  logLevelTone,
  logSourceLabel,
  sanitizeLogMessage,
} from "./log";

describe("unified log model", () => {
  it("maps levels to Chinese labels and tones", () => {
    expect(logLevelLabel("error")).toBe("错误");
    expect(logLevelLabel("warning")).toBe("警告");
    expect(logLevelLabel("info")).toBe("信息");
    expect(logLevelLabel("verbose")).toBe("详细");
    expect(logLevelTone("error")).toBe("error");
    expect(logLevelTone("warning")).toBe("warning");
    expect(logLevelTone("info")).toBe("default");
  });

  it("labels log sources", () => {
    expect(logSourceLabel("main")).toBe("主进程");
    expect(logSourceLabel("renderer")).toBe("渲染器");
    expect(logSourceLabel("api")).toBe("API");
    expect(logSourceLabel("worker")).toBe("Worker");
    expect(logSourceLabel("system")).toBe("系统");
  });

  it("infers level from common error/warning keywords", () => {
    expect(inferLogLevel("Traceback (most recent call last)")).toBe("error");
    expect(inferLogLevel("ERROR: policy gateway blocked capability")).toBe("error");
    expect(inferLogLevel("WARNING: retry in 2s")).toBe("warning");
    expect(inferLogLevel("INFO: worker polled outbox")).toBe("info");
    expect(inferLogLevel("GET /health 200")).toBe("info");
  });

  it("infers source from log file name prefixes", () => {
    expect(inferLogSource("api.log")).toBe("api");
    expect(inferLogSource("api.err.log")).toBe("api");
    expect(inferLogSource("worker.log")).toBe("worker");
    expect(inferLogSource("postgres16.log")).toBe("system");
    expect(inferLogSource("unified.log")).toBe("system");
  });

  it("redacts sensitive key-value pairs in messages", () => {
    expect(sanitizeLogMessage("Authorization: Bearer abc.def.ghi")).toBe("Authorization: ***");
    expect(sanitizeLogMessage("api_key=sk-1234567890")).toBe("api_key=***");
    expect(sanitizeLogMessage('password: "hunter2"')).toBe("password: ***");
    expect(sanitizeLogMessage("token = 0xdeadbeef, kept")).toBe("token = ***, kept");
    expect(sanitizeLogMessage("GET /health 200 ok")).toBe("GET /health 200 ok");
  });

  it("formats ISO timestamps as local clock", () => {
    const iso = new Date(2026, 8, 8, 14, 5, 7, 123).toISOString();
    expect(formatLogTimestamp(iso)).toMatch(/^\d{2}:\d{2}:\d{2}\.\d{3}$/);
    expect(formatLogTimestamp("not-a-date")).toBe("not-a-date");
  });
});
