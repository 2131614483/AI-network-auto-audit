// 统一日志模型：来源/级别标签、消息脱敏与行解析纯函数。
// 不依赖任何运行时，供渲染器日志窗口与单测复用。

export type LogLevel = "verbose" | "info" | "warning" | "error";
export type LogSource = "main" | "renderer" | "api" | "worker" | "system";

export type LogEntry = {
  id: number;
  ts: string;
  source: LogSource;
  level: LogLevel;
  message: string;
};

export const LOG_SOURCES: Array<{ value: LogSource | "all"; label: string }> = [
  { value: "all", label: "全部来源" },
  { value: "main", label: "主进程" },
  { value: "renderer", label: "渲染器" },
  { value: "api", label: "API" },
  { value: "worker", label: "Worker" },
  { value: "system", label: "系统" },
];

export const LOG_LEVELS: Array<{ value: LogLevel | "all"; label: string }> = [
  { value: "all", label: "全部级别" },
  { value: "info", label: "信息" },
  { value: "warning", label: "警告" },
  { value: "error", label: "错误" },
];

export function logLevelLabel(level: LogLevel): string {
  switch (level) {
    case "error": return "错误";
    case "warning": return "警告";
    case "verbose": return "详细";
    default: return "信息";
  }
}

export function logLevelTone(level: LogLevel): "default" | "warning" | "error" {
  if (level === "error") return "error";
  if (level === "warning") return "warning";
  return "default";
}

export function logSourceLabel(source: LogSource): string {
  switch (source) {
    case "main": return "主进程";
    case "renderer": return "渲染器";
    case "api": return "API";
    case "worker": return "Worker";
    default: return "系统";
  }
}

// 日志行级别推断：优先匹配错误/警告关键字，其余视为信息。
export function inferLogLevel(line: string): LogLevel {
  const upper = line.toUpperCase();
  if (/ERROR|CRITICAL|FATAL|TRACEBACK|EXCEPTION|FAILED|REFUSED/.test(upper)) return "error";
  if (/WARN(ING)?|DEPRECAT/.test(upper)) return "warning";
  return "info";
}

// 日志文件来源推断：文件名前缀 -> 来源。
export function inferLogSource(fileName: string): LogSource {
  const lower = fileName.toLowerCase();
  if (lower.startsWith("worker")) return "worker";
  if (lower.startsWith("api")) return "api";
  return "system";
}

const SENSITIVE_PATTERN =
  /\b(authorization|api[_-]?key|secret|password|passwd|token|access[_-]?token|refresh[_-]?token|private[_-]?key|cookie)\b(\s*[:=]\s*)(?:"[^"]*"|'[^']*'|Bearer\s+\S+|[^\s,;]+)/gi;

// 消息脱敏：把常见密钥/令牌键值替换为 ***，避免调试日志回显敏感材料。
export function sanitizeLogMessage(message: string): string {
  return message.replace(SENSITIVE_PATTERN, (match, key: string, separator: string) => `${key}${separator}***`);
}

export function formatLogTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}
