import { app, BrowserWindow, dialog, ipcMain, Menu, type IpcMainInvokeEvent } from "electron";
import { appendFileSync, closeSync, existsSync, openSync, readFileSync, readdirSync, readSync, renameSync, statSync } from "node:fs";
import { readFile, readdir, stat, writeFile } from "node:fs/promises";
import { basename, extname, join, relative, resolve, sep } from "node:path";

const MIN_ZOOM_FACTOR = 0.8;
const MAX_ZOOM_FACTOR = 2.0;
const zoomStorePath = () => join(app.getPath("userData"), "window-zoom.json");

// ===== 统一日志收集（主进程 / 渲染器 / API / Worker 日志文件） =====
type LogLevel = "verbose" | "info" | "warning" | "error";
type LogSource = "main" | "renderer" | "api" | "worker" | "system";
type LogEntry = { id: number; ts: string; source: LogSource; level: LogLevel; message: string };

const MAX_UNIFIED_LOGS = 5000;
const unifiedLogs: LogEntry[] = [];
let logSequence = 0;
let unifiedLogPath = "";
let unifiedLogDate = "";
const logWindows = new Set<BrowserWindow>();

function logDataDir(): string {
  const candidates = [
    join(app.getAppPath(), "..", ".data"),
    join(app.getAppPath(), "..", "..", ".data"),
    join(process.cwd(), ".data"),
  ];
  for (const candidate of candidates) {
    try { if (existsSync(candidate)) return candidate; } catch { /* 继续尝试下一个候选 */ }
  }
  return candidates[0];
}

const SENSITIVE_LOG_PATTERN =
  /\b(authorization|api[_-]?key|secret|password|passwd|token|access[_-]?token|refresh[_-]?token|private[_-]?key|cookie)\b(\s*[:=]\s*)(?:"[^"]*"|'[^']*'|Bearer\s+\S+|[^\s,;]+)/gi;

function sanitizeLogMessage(message: string): string {
  return message.replace(SENSITIVE_LOG_PATTERN, (match, key: string, separator: string) => `${key}${separator}***`);
}

function formatLogFileLine(entry: LogEntry): string {
  return `[${entry.ts}] [${entry.source}] [${entry.level}] ${entry.message}\n`;
}

// 按日归档：unified-YYYY-MM-DD.log；启动时把早期 unified.log 迁移到当天文件。
function resolveUnifiedLogPath(): string {
  const today = new Date().toISOString().slice(0, 10);
  const dataDir = logDataDir();
  const target = join(dataDir, `unified-${today}.log`);
  if (!existsSync(target)) {
    const legacy = join(dataDir, "unified.log");
    try {
      if (existsSync(legacy) && statSync(legacy).size > 0) renameSync(legacy, target);
    } catch { /* 迁移失败不阻断，继续写目标文件 */ }
  }
  return target;
}

function emitLog(source: LogSource, level: LogLevel, message: string): void {
  const entry: LogEntry = {
    id: ++logSequence,
    ts: new Date().toISOString(),
    source,
    level,
    message: sanitizeLogMessage(message).slice(0, 4000),
  };
  unifiedLogs.push(entry);
  if (unifiedLogs.length > MAX_UNIFIED_LOGS) {
    unifiedLogs.splice(0, unifiedLogs.length - MAX_UNIFIED_LOGS);
  }
  const today = new Date().toISOString().slice(0, 10);
  if (today !== unifiedLogDate) {
    unifiedLogDate = today;
    unifiedLogPath = resolveUnifiedLogPath();
  }
  if (unifiedLogPath) {
    try { appendFileSync(unifiedLogPath, formatLogFileLine(entry), "utf-8"); } catch { /* 日志落盘失败不阻断界面 */ }
  }
  for (const window of logWindows) {
    if (!window.isDestroyed()) window.webContents.send("audit:logEvent", entry);
  }
}

function formatConsoleArgs(args: unknown[]): string {
  return args.map((arg) => {
    if (typeof arg === "string") return arg;
    if (arg instanceof Error) return arg.stack ?? arg.message;
    try { return JSON.stringify(arg); } catch { return String(arg); }
  }).join(" ");
}

// 捕获主进程 console：统一写入缓冲、落盘并推送日志窗口。
function installMainConsoleCapture(): void {
  const original = { log: console.log, info: console.info, warn: console.warn, error: console.error };
  console.log = (...args: unknown[]) => { original.log(...args); emitLog("main", "info", formatConsoleArgs(args)); };
  console.info = (...args: unknown[]) => { original.info(...args); emitLog("main", "info", formatConsoleArgs(args)); };
  console.warn = (...args: unknown[]) => { original.warn(...args); emitLog("main", "warning", formatConsoleArgs(args)); };
  console.error = (...args: unknown[]) => { original.error(...args); emitLog("main", "error", formatConsoleArgs(args)); };
}

// 捕获渲染器 console（Electron 事件签名：event, level(0-3), message, line, sourceId）。
function installRendererConsoleCapture(window: BrowserWindow): void {
  window.webContents.on("console-message", (_event, level, message) => {
    const logLevel: LogLevel = level === 3 ? "error" : level === 2 ? "warning" : "info";
    emitLog("renderer", logLevel, message);
  });
}

// 尾部轮询 .data/*.log（api/worker 等），增量行进入统一日志；文件轮转时从头重读。
function installLogFileWatcher(): void {
  interface FileSource { path: string; offset: number; source: LogSource }
  const sources: FileSource[] = [];
  const readTail = (): void => {
    const dataDir = logDataDir();
    let fileNames: string[] = [];
    try {
      fileNames = readdirSync(dataDir)
        .filter((name) => name.endsWith(".log") && !name.startsWith("unified")
          && !name.startsWith("electron") && !name.startsWith("desktop") && !name.startsWith("renderer"));
    } catch { return; }
    for (const name of fileNames) {
      const path = join(dataDir, name);
      if (!sources.some((source) => source.path === path)) {
        try { sources.push({ path, offset: statSync(path).size, source: name.startsWith("worker") ? "worker" : name.startsWith("api") ? "api" : "system" }); } catch { /* 文件瞬时不可读跳过 */ }
      }
    }
    for (const source of sources) {
      try {
        const size = statSync(source.path).size;
        if (size < source.offset) source.offset = 0; // 文件被截断/轮转
        if (size <= source.offset) continue;
        const fd = openSync(source.path, "r");
        const buffer = Buffer.alloc(size - source.offset);
        readSync(fd, buffer, 0, buffer.length, source.offset);
        closeSync(fd);
        source.offset = size;
        for (const raw of buffer.toString("utf-8").split(/\r?\n/)) {
          const line = raw.trim();
          if (!line) continue;
          const upper = line.toUpperCase();
          const level: LogLevel = /ERROR|CRITICAL|FATAL|TRACEBACK|EXCEPTION|FAILED|REFUSED/.test(upper)
            ? "error" : /WARN(ING)?|DEPRECAT/.test(upper) ? "warning" : "info";
          emitLog(source.source, level, line);
        }
      } catch { /* 日志文件被占用或删除，下一轮重试 */ }
    }
  };
  readTail();
  setInterval(readTail, 1500);
}

function createLogWindow(): void {
  for (const window of logWindows) {
    if (!window.isDestroyed()) { window.focus(); return; }
  }
  const window = new BrowserWindow({
    width: 1080,
    height: 720,
    minWidth: 720,
    minHeight: 420,
    title: "统一日志 · Audit Network Debug",
    backgroundColor: "#0d0e10",
    autoHideMenuBar: true,
    webPreferences: { preload: join(__dirname, "../preload/index.js"), contextIsolation: true, sandbox: true },
  });
  logWindows.add(window);
  window.on("closed", () => logWindows.delete(window));
  installRendererConsoleCapture(window);
  if (process.env.ELECTRON_RENDERER_URL) void window.loadURL(`${process.env.ELECTRON_RENDERER_URL}?view=log`);
  else void window.loadFile(join(__dirname, "../renderer/index.html"), { query: { view: "log" } });
}

function createAuditBrainWindow(): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (window.getTitle().includes("审计插件大脑")) { window.focus(); return; }
  }
  const window = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 960,
    minHeight: 640,
    title: "审计插件大脑 · 树状组网与数据接口流",
    backgroundColor: "#070b12",
    autoHideMenuBar: true,
    webPreferences: { preload: join(__dirname, "../preload/index.js"), contextIsolation: true, sandbox: true },
  });
  window.maximize();
  const capturePath = process.env.AUDIT_NETWORK_CAPTURE_BRAIN_PATH;
  if (capturePath) {
    window.webContents.once("did-finish-load", () => {
      setTimeout(() => {
        void window.webContents.capturePage().then((image) => writeFile(capturePath, image.toPNG()));
      }, 8000);
    });
  }
  if (process.env.ELECTRON_RENDERER_URL) void window.loadURL(`${process.env.ELECTRON_RENDERER_URL}/audit-brain.html`);
  else void window.loadFile(join(__dirname, "../renderer/audit-brain.html"));
}

function createKnowledgeNebulaWindow(): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (window.getTitle().includes("知识星云")) { window.focus(); return; }
  }
  const window = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 960,
    minHeight: 640,
    title: "知识星云 · 审计插件动态知识图谱",
    backgroundColor: "#05070e",
    autoHideMenuBar: true,
    webPreferences: { preload: join(__dirname, "../preload/index.js"), contextIsolation: true, sandbox: true },
  });
  window.maximize();
  const capturePath = process.env.AUDIT_NETWORK_CAPTURE_NEBULA_PATH;
  if (capturePath) {
    let selftestApplied = false;
    window.webContents.on("did-finish-load", () => {
      // 验收钩子：首次注入本地编辑层（手动边）并重载，第二次加载后截图，
      // 用于证明手动连线经 localStorage 持久化并叠加到后端自动图谱。
      const selftest = process.env.AUDIT_NETWORK_NEBULA_SELFTEST;
      if (selftest && !selftestApplied) {
        selftestApplied = true;
        void window.webContents.executeJavaScript(
          `localStorage.setItem('audit.nebula.edits.local-dev', ${JSON.stringify(selftest)});location.reload();`,
        );
        return;
      }
      // 轮询直到后端动态图加载完成（徽标变「实时」）或 15s 超时，再开图层截图
      const deadline = Date.now() + 15000;
      const captureWhenLive = (): void => {
        void window.webContents
          .executeJavaScript(
            "(document.getElementById('srcBadge')||{}).textContent||''",
          )
          .then((badge) => {
            if (String(badge).includes("实时") || Date.now() > deadline) {
              if (process.env.AUDIT_NETWORK_NEBULA_DATAFLOW === "1") {
                void window.webContents.executeJavaScript(
                  "var cb=document.getElementById('lvDataflow');if(cb&&!cb.checked)cb.click();",
                );
              }
              if (process.env.AUDIT_NETWORK_NEBULA_CROSS === "1") {
                void window.webContents.executeJavaScript(
                  "var cb=document.getElementById('lvCross');if(cb&&!cb.checked)cb.click();",
                );
              }
              if (process.env.AUDIT_NETWORK_NEBULA_EXPERIENCE === "1") {
                void window.webContents.executeJavaScript(
                  "window.__nebulaExperienceSelfTest&&window.__nebulaExperienceSelfTest();",
                ).then(() => {
                  const detail = process.env.AUDIT_NETWORK_NEBULA_EXP_DETAIL;
                  if (detail === "node") {
                    return window.webContents.executeJavaScript(
                      "window.__nebulaOpenNodeDetail&&window.__nebulaOpenNodeDetail('s1-06');",
                    );
                  }
                  if (detail === "suggestion") {
                    return window.webContents.executeJavaScript(
                      "window.__nebulaOpenSuggestionDetail&&window.__nebulaOpenSuggestionDetail();",
                    );
                  }
                  if (detail === "confirmed") {
                    return window.webContents.executeJavaScript(
                      "window.__nebulaOpenSuggestionDetail&&window.__nebulaOpenSuggestionDetail('confirmed');",
                    );
                  }
                  return undefined;
                });
              }
              setTimeout(() => {
                void window.webContents
                  .capturePage()
                  .then((image) => writeFile(capturePath, image.toPNG()));
              }, 1700);
            } else {
              setTimeout(captureWhenLive, 500);
            }
          });
      };
      setTimeout(captureWhenLive, 1500);
    });
  }
  if (process.env.ELECTRON_RENDERER_URL) void window.loadURL(`${process.env.ELECTRON_RENDERER_URL}/knowledge-nebula.html`);
  else void window.loadFile(join(__dirname, "../renderer/knowledge-nebula.html"));
}

function createFlowCanvasWindow(): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (window.getTitle().includes("数据流演示")) { window.focus(); return; }
  }
  const window = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 960,
    minHeight: 640,
    title: "Run 画布 · 数据流演示",
    backgroundColor: "#05070e",
    autoHideMenuBar: true,
    webPreferences: { preload: join(__dirname, "../preload/index.js"), contextIsolation: true, sandbox: true },
    show: !process.env.AUDIT_NETWORK_CAPTURE_FLOW_PATH,
  });
  const capturePath = process.env.AUDIT_NETWORK_CAPTURE_FLOW_PATH;
  if (capturePath) {
    window.webContents.on("did-finish-load", () => {
      const atMs = Number(process.env.AUDIT_NETWORK_FLOW_AT_MS ?? 0);
      const finalPath = capturePath.replace(/\.png$/i, "-final.png");
      setTimeout(() => {
        void window.webContents.executeJavaScript(`window.__flowDemo&&window.__flowDemo.seek(${atMs})`)
          .then(() => new Promise((r) => setTimeout(r, 650)))
          .then(() => window.webContents.capturePage())
          .then((image) => writeFile(capturePath, image.toPNG()))
          .then(() => window.webContents.executeJavaScript("window.__flowDemo.seek(window.__flowDemo.info().durationMs)"))
          .then(() => new Promise((r) => setTimeout(r, 650)))
          .then(() => window.webContents.capturePage())
          .then((image) => writeFile(finalPath, image.toPNG()))
          .then(() => setTimeout(() => app.quit(), 400));
      }, 900);
    });
  }
  if (process.env.ELECTRON_RENDERER_URL) void window.loadURL(`${process.env.ELECTRON_RENDERER_URL}/flow-canvas-demo.html`);
  else void window.loadFile(join(__dirname, "../renderer/flow-canvas-demo.html"));
}

function clampZoom(factor: number): number {
  return Math.min(MAX_ZOOM_FACTOR, Math.max(MIN_ZOOM_FACTOR, factor));
}

async function loadSavedZoom(): Promise<number | null> {
  try {
    const raw = await readFile(zoomStorePath(), "utf-8");
    const value = Number(JSON.parse(raw).zoomFactor);
    return Number.isFinite(value) ? clampZoom(value) : null;
  } catch {
    return null;
  }
}

function saveZoom(factor: number): void {
  void writeFile(zoomStorePath(), JSON.stringify({ zoomFactor: clampZoom(factor) }), "utf-8").catch(() => {
    // 缩放开不了持久化文件不阻断使用。
  });
}

function applyZoomStep(window: BrowserWindow, step: number): void {
  const contents = window.webContents;
  contents.setZoomFactor(clampZoom(contents.getZoomFactor() + step));
  saveZoom(contents.getZoomFactor());
}

function resetZoom(window: BrowserWindow): void {
  window.webContents.setZoomFactor(1);
  saveZoom(1);
}

// 初始缩放同步应用：避免渲染器先读 get、后应用保存值的启动竞态。
function installWindowZoom(window: BrowserWindow, initialZoom: number | null): void {
  const contents = window.webContents;
  // 注意：不要在这里用 visual-zoom 上限来禁用捏合缩放——把上下限设为 1,1 存在
  // 导致鼠标滚轮完全无法滚动页面的已知问题。Ctrl+滚轮/捏合缩放已改在渲染器
  // main.tsx 里通过 preventDefault 拦截，普通滚动不受影响。
  if (initialZoom !== null) contents.setZoomFactor(initialZoom);
}

function installApplicationMenu(): void {
  const zoomStep = (step: number) => {
    const window = BrowserWindow.getFocusedWindow();
    if (window) applyZoomStep(window, step);
  };
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: "视图",
      submenu: [
        { label: "放大", accelerator: "CmdOrCtrl+=", click: () => zoomStep(0.1) },
        { label: "缩小", accelerator: "CmdOrCtrl+-", click: () => zoomStep(-0.1) },
        { label: "重置缩放", accelerator: "CmdOrCtrl+0", click: () => {
            const window = BrowserWindow.getFocusedWindow();
            if (window) resetZoom(window);
          } },
      ],
    },
  ]));
}

const controlPlaneUrl = (process.env.AUDIT_NETWORK_API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");
const allowedPaths = new Set([
  "/health",
  "/api/v1/ui/bootstrap",
  "/api/v1/ui/contributions",
  "/api/v1/ui/tenant-context",
  "/api/v1/ui/operations",
  "/api/v1/ui/operations/detail",
  "/api/v1/plugins/verified",
  "/api/v1/graph/visualization",
  "/api/v1/graph/routes",
  "/api/v1/graph/governance",
  "/api/v1/graph/conflicts",
  "/api/v1/graph/merges",
  "/api/v1/graph/splits",
  "/api/v1/graph/extractions/preview",
  "/api/v1/graph/extractions/propose",
  "/api/v1/graph/extractions/proposals",
  "/api/v1/audit/engagements",
  "/api/v1/audit/findings/confirm",
  "/api/v1/quant/backtests",
  "/api/v1/aiops/incidents",
  "/api/v1/topology/clusters",
  "/api/v1/topology/blueprints",
  "/api/v1/topology/plugin-nebula",
  "/api/v1/topology/plugin-nebula/experience",
  "/api/v1/topology/nebula-experience/suggestions",
  "/api/v1/topology/nebula-experience/rebuild",
  "/api/v1/topology/releases",
  "/api/v1/topology/plans",
  "/api/v1/topology/bridges",
  "/api/v1/topology/chains",
  "/api/v1/approvals",
  "/api/v1/policy/simulate",
  "/api/v1/knowledge/upload",
  "/api/v1/knowledge/stats",
  "/api/v1/knowledge/documents",
  "/api/v1/knowledge/adapters",
  "/api/v1/knowledge/batches",
  "/api/v1/knowledge/search",
  "/api/v1/knowledge/embed",
  "/api/v1/knowledge/recycle-bin",
  "/api/v1/knowledge/rich-media/pending",
  "/api/v1/knowledge/rich-media/extract",
  "/api/v1/knowledge/rich-media/retry",
  "/api/v1/topology/planning/intents",
  "/api/v1/topology/canvas/chat",
  "/api/v1/topology/canvas/chat/stream",
  // Unified AI access: read/write the provider configuration + connectivity probe.
  "/api/v1/ai/settings",
  "/api/v1/ai/test",
]);

type SafeRequest = {
  path: string;
  method?: "GET" | "POST" | "PUT";
  tenantId?: string;
  tenantSlug?: string;
  body?: Record<string, unknown>;
  query?: Record<string, string | number | boolean | undefined>;
};

type WindowControlAction = "minimize" | "toggle-maximize" | "close" | "query";
type KnowledgeSelectionFile = { path: string; relativePath: string; sizeBytes: number };
type KnowledgeSelection = {
  selectionId: string;
  sourceKind: "files" | "folder";
  files: Array<{ relativePath: string; sizeBytes: number }>;
};

const MAX_KNOWLEDGE_SELECTION_FILES = 1_000;
const MAX_KNOWLEDGE_UPLOAD_BATCH_FILES = 20;
const SUPPORTED_KNOWLEDGE_SUFFIXES = new Set([
  ".md", ".markdown", ".txt", ".csv", ".docx", ".xlsx",
  ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp",
  ".mp3", ".wav", ".m4a", ".flac", ".aac", ".mp4", ".mov", ".mkv", ".avi", ".webm",
]);
const knowledgeSelections = new Map<string, KnowledgeSelectionFile[]>();

function isAllowedControlPlanePath(path: string): boolean {
  if (allowedPaths.has(path)) return true;
  return /^\/api\/v1\/knowledge\/documents\/[0-9a-f-]{36}\/retire$/i.test(path)
    || /^\/api\/v1\/knowledge\/recycle-bin\/[0-9a-f-]{36}\/restore$/i.test(path)
    || /^\/api\/v1\/graph\/nodes\/[0-9a-f-]{36}\/revisions$/i.test(path)
    || /^\/api\/v1\/graph\/extractions\/proposals\/[0-9a-f-]{36}\/(?:approve|reject)$/i.test(path)
    || /^\/api\/v1\/audit\/engagements\/[0-9a-f-]{36}\/lineage$/i.test(path)
    || /^\/api\/v1\/quant\/backtests\/[0-9a-f-]{36}\/lineage$/i.test(path)
    || /^\/api\/v1\/aiops\/incidents\/[0-9a-f-]{36}\/lineage$/i.test(path)
    || /^\/api\/v1\/aiops\/executions\/[0-9a-f-]{36}\/verify$/i.test(path)
    || /^\/api\/v1\/topology\/chains\/chain-[a-z0-9]{16}\/intents$/i.test(path)
    || /^\/api\/v1\/topology\/chains\/chain-[a-z0-9]{16}\/approvals$/i.test(path)
    || /^\/api\/v1\/topology\/chains\/chain-[a-z0-9]{16}\/executions$/i.test(path)
    || /^\/api\/v1\/topology\/chains\/chain-[a-z0-9]{16}\/runs$/i.test(path)
    || /^\/api\/v1\/topology\/runs\/[0-9a-f-]{36}$/i.test(path)
    || /^\/api\/v1\/topology\/runs\/[0-9a-f-]{36}\/verifications$/i.test(path)
    || /^\/api\/v1\/topology\/verifications\/[0-9a-f-]{36}$/i.test(path)
    || /^\/api\/v1\/topology\/verifications\/[0-9a-f-]{36}\/remediation$/i.test(path)
    || /^\/api\/v1\/topology\/remediation$/i.test(path)
    || /^\/api\/v1\/topology\/remediation\/[0-9a-f-]{36}$/i.test(path)
    || /^\/api\/v1\/topology\/remediation\/[0-9a-f-]{36}\/decisions$/i.test(path)
    || /^\/api\/v1\/topology\/remediation\/[0-9a-f-]{36}\/runs$/i.test(path)
    || /^\/api\/v1\/topology\/evidence\/anchors$/i.test(path)
    || /^\/api\/v1\/topology\/evidence\/verify$/i.test(path)
    || /^\/api\/v1\/topology\/evidence\/export$/i.test(path)
    || /^\/api\/v1\/topology\/evidence\/status$/i.test(path)
    || /^\/api\/v1\/topology\/runs$/i.test(path)
    || /^\/api\/v1\/topology\/canvas\/[0-9a-f-]{36}$/i.test(path)
    || /^\/api\/v1\/topology\/planning\/intents\/[0-9a-f-]{36}$/i.test(path)
    || /^\/api\/v1\/topology\/nebula-experience\/suggestions\/[0-9a-f-]{36}\/decision$/i.test(path);
}

function headersFor(request: SafeRequest): Headers {
  const headers = new Headers({ "X-Trace-Id": crypto.randomUUID() });
  if (request.tenantId) headers.set("X-Tenant-Id", request.tenantId);
  if (request.tenantSlug) headers.set("X-Tenant-Slug", request.tenantSlug);
  if (request.method === "POST" || request.method === "PUT") {
    headers.set("Idempotency-Key", crypto.randomUUID());
  }
  return headers;
}

function readableControlPlaneError(payload: unknown): string {
  const detail = typeof payload === "object" && payload !== null && "detail" in payload ? payload.detail : undefined;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    const message = detail.message;
    if (message === "policy gateway blocked capability") {
      return "策略网关尚未允许此操作；请在审批中心按最小权限发布对应规则。";
    }
    if (typeof message === "string") return message;
  }
  return "控制平面拒绝了该请求；请检查租户上下文、审批状态和接口规则。";
}

async function controlPlaneRequest(request: SafeRequest): Promise<unknown> {
  if (!isAllowedControlPlanePath(request.path)) throw new Error("桌面端拒绝未声明的控制平面接口。");
  const headers = headersFor(request);
  const init: RequestInit = { method: request.method ?? "GET", headers };
  if (request.body) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(request.body);
  }
  let response: Response;
  try {
    const url = new URL(`${controlPlaneUrl}${request.path}`);
    for (const [key, value] of Object.entries(request.query ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
    response = await fetch(url, init);
  } catch {
    throw new Error("无法连接本机控制平面。请先启动 Audit Network 服务后重试。");
  }
  const payload: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(readableControlPlaneError(payload));
  }
  return payload;
}

// --- CW4: run feed subscription (idempotent retry + catch-up after drop) ----
// The main process owns the poller so the desktop keeps receiving run state
// even if a renderer view is closed; GET reads are safe to retry.  On failure
// the last good snapshot is kept and marked offline; the next successful poll
// catches the feed back up without any manual refresh.

type RunsFeedState = {
  tenantId: string;
  items: unknown[];
  traceId: string;
  updatedAt: number;
  offline: boolean;
};
const runsFeedState: RunsFeedState = { tenantId: "", items: [], traceId: "", updatedAt: 0, offline: false };
let runsFeedTimer: NodeJS.Timeout | null = null;

async function pollRunsFeed(tenantId: string): Promise<void> {
  try {
    const payload = (await controlPlaneRequest({
      path: "/api/v1/topology/runs",
      tenantId,
      query: { limit: 50 },
    })) as { items?: unknown[]; trace_id?: string };
    runsFeedState.tenantId = tenantId;
    runsFeedState.items = Array.isArray(payload.items) ? payload.items : [];
    runsFeedState.traceId = String(payload.trace_id ?? "");
    runsFeedState.updatedAt = Date.now();
    runsFeedState.offline = false;
  } catch {
    runsFeedState.offline = true;
  }
}

function startRunsFeed(tenantId: string): void {
  if (runsFeedTimer) {
    clearInterval(runsFeedTimer);
    runsFeedTimer = null;
  }
  if (runsFeedState.tenantId !== tenantId) {
    runsFeedState.items = [];
    runsFeedState.updatedAt = 0;
    runsFeedState.tenantId = tenantId;
  }
  runsFeedTimer = setInterval(() => void pollRunsFeed(tenantId), 30_000);
}

async function ensureRunsFeed(tenantId: string): Promise<RunsFeedState> {
  if (runsFeedState.tenantId !== tenantId) {
    // First subscription: await the catch-up poll so the renderer never
    // receives an empty frame while the poll is still in flight.
    startRunsFeed(tenantId);
    await pollRunsFeed(tenantId);
  }
  return { ...runsFeedState, items: [...runsFeedState.items] };
}

ipcMain.handle("audit:runsSnapshot", (event, tenantId: string): Promise<RunsFeedState> | RunsFeedState => {
  if (!tenantId) return { ...runsFeedState, items: [...runsFeedState.items] };
  return ensureRunsFeed(tenantId);
});

function isSupportedKnowledgeFile(path: string): boolean {
  return SUPPORTED_KNOWLEDGE_SUFFIXES.has(extname(path).toLowerCase());
}

function safeRelativePath(root: string | undefined, filePath: string): string {
  const relativePath = root ? relative(root, filePath) : basename(filePath);
  if (!relativePath || relativePath.startsWith("..") || relativePath.includes(".." + sep)) {
    throw new Error("知识文件不在已选择的目录内。");
  }
  return relativePath.split(sep).join("/");
}

async function createKnowledgeSelection(
  files: string[],
  sourceKind: "files" | "folder",
  root?: string,
): Promise<KnowledgeSelection | null> {
  const selected: KnowledgeSelectionFile[] = [];
  for (const filePath of files) {
    if (!isSupportedKnowledgeFile(filePath)) continue;
    const fileStats = await stat(filePath);
    if (!fileStats.isFile()) continue;
    if (selected.length >= MAX_KNOWLEDGE_SELECTION_FILES) {
      throw new Error(`一次最多选择 ${MAX_KNOWLEDGE_SELECTION_FILES} 个可支持文件，请分批导入。`);
    }
    selected.push({ path: filePath, relativePath: safeRelativePath(root, filePath), sizeBytes: fileStats.size });
  }
  if (!selected.length) return null;
  const selectionId = crypto.randomUUID();
  knowledgeSelections.set(selectionId, selected);
  return {
    selectionId,
    sourceKind,
    files: selected.map(({ relativePath, sizeBytes }) => ({ relativePath, sizeBytes })),
  };
}

async function collectFolderKnowledgeFiles(root: string): Promise<string[]> {
  const files: string[] = [];
  const pending = [root];
  while (pending.length) {
    const current = pending.pop();
    if (!current) continue;
    const entries = await readdir(current, { withFileTypes: true });
    for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
      if (entry.isSymbolicLink()) continue;
      const candidate = join(current, entry.name);
      if (entry.isDirectory()) pending.push(candidate);
      else if (entry.isFile() && isSupportedKnowledgeFile(candidate)) files.push(candidate);
      if (files.length > MAX_KNOWLEDGE_SELECTION_FILES) {
        throw new Error(`文件夹超过 ${MAX_KNOWLEDGE_SELECTION_FILES} 个可支持文件，请分批导入。`);
      }
    }
  }
  return files.sort((left, right) => left.localeCompare(right));
}

async function uploadKnowledgeBatch(tenantId: string, files: KnowledgeSelectionFile[]): Promise<unknown> {
  if (!allowedPaths.has("/api/v1/knowledge/upload")) throw new Error("知识导入接口未获授权。");
  const headers = headersFor({ path: "/api/v1/knowledge/upload", method: "POST", tenantId });
  const form = new FormData();
  for (const file of files) {
    const content = await readFile(file.path);
    form.append("files", new Blob([content]), basename(file.relativePath));
    form.append("relative_paths", file.relativePath);
  }
  let response: Response;
  try {
    response = await fetch(`${controlPlaneUrl}/api/v1/knowledge/upload`, { method: "POST", headers, body: form });
  } catch {
    throw new Error("无法连接本机控制平面。知识文件未被上传或处理。");
  }
  const payload: unknown = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload === "object" && payload !== null && "detail" in payload ? String(payload.detail) : "知识导入失败");
  return payload;
}

async function uploadKnowledge(tenantId: string, selectionId: string): Promise<unknown> {
  const selected = knowledgeSelections.get(selectionId);
  if (!selected?.length) throw new Error("知识文件选择已失效，请重新选择文件或文件夹。");
  const batches: unknown[] = [];
  for (let start = 0; start < selected.length; start += MAX_KNOWLEDGE_UPLOAD_BATCH_FILES) {
    batches.push(await uploadKnowledgeBatch(tenantId, selected.slice(start, start + MAX_KNOWLEDGE_UPLOAD_BATCH_FILES)));
  }
  return batches.length === 1 ? batches[0] : { selection_id: selectionId, batch_count: batches.length, batches };
}

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url;
  if (!senderUrl || (!senderUrl.startsWith("file:") && !senderUrl.startsWith("http://localhost:"))) {
    throw new Error("拒绝来自非受信渲染器的调用。");
  }
}

function createWindow(initialZoom: number | null): void {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    backgroundColor: "#0d0e10",
    autoHideMenuBar: true,
    titleBarStyle: "hidden",
    webPreferences: { preload: join(__dirname, "../preload/index.js"), contextIsolation: true, sandbox: true },
  });
  installWindowZoom(window, initialZoom);
  installApplicationMenu();
  installRendererConsoleCapture(window);
  // 默认最大化启动：避免在高分辨率屏幕上窗口只占半屏、界面显得过小。
  window.maximize();
  const capturePath = process.env.AUDIT_NETWORK_CAPTURE_PATH;
  const captureView = process.env.AUDIT_NETWORK_CAPTURE_VIEW;
  if (capturePath) {
    const configuredCaptureDelay = Number(process.env.AUDIT_NETWORK_CAPTURE_DELAY_MS ?? "3500");
    const captureDelayMs = Number.isFinite(configuredCaptureDelay)
      ? Math.min(30_000, Math.max(500, Math.trunc(configuredCaptureDelay)))
      : 3500;
    window.webContents.once("did-finish-load", () => {
      setTimeout(() => {
        void window.webContents.capturePage().then((image) => writeFile(capturePath, image.toPNG()));
      }, captureDelayMs);
    });
  }
  if (process.env.ELECTRON_RENDERER_URL) {
    const url = new URL(process.env.ELECTRON_RENDERER_URL);
    if (captureView) applyViewQuery(url, captureView);
    void window.loadURL(url.toString());
  }
  else {
    const query = captureView ? viewQuery(captureView) : {};
    void window.loadFile(
      join(__dirname, "../renderer/index.html"),
      { query },
    );
  }
}

/** "view=xxx&canvas=yyy" -> { view: "xxx", canvas: "yyy" } */
function viewQuery(captureView: string): Record<string, string> {
  const query: Record<string, string> = {};
  for (const pair of captureView.split("&")) {
    const idx = pair.indexOf("=");
    if (idx > 0) query[pair.slice(0, idx)] = pair.slice(idx + 1);
    else if (pair) query[pair] = "";
  }
  return query;
}

function applyViewQuery(url: URL, captureView: string): void {
  for (const [key, value] of Object.entries(viewQuery(captureView))) {
    if (value) url.searchParams.set(key, value);
  }
}

app.whenReady().then(async () => {
  ipcMain.handle("audit:request", (event, request: SafeRequest) => {
    assertTrustedSender(event);
    return controlPlaneRequest(request);
  });
  const activeStreams = new Map<string, AbortController>();
  ipcMain.on("audit:stream:start", (event, payload: { streamId: string; path: string; tenantId?: string; body?: Record<string, unknown> }) => {
    const sender = event.sender;
    const senderUrl = event.senderFrame?.url;
    if (!senderUrl || (!senderUrl.startsWith("file:") && !senderUrl.startsWith("http://localhost:"))) {
      sender.send("audit:stream:end", { streamId: payload.streamId, error: "拒绝来自非受信渲染器的调用。" });
      return;
    }
    if (!isAllowedControlPlanePath(payload.path)) {
      sender.send("audit:stream:end", { streamId: payload.streamId, error: "桌面端拒绝未声明的控制平面接口。" });
      return;
    }
    const controller = new AbortController();
    activeStreams.set(payload.streamId, controller);
    const headers = new Headers({ "X-Trace-Id": crypto.randomUUID(), "Content-Type": "application/json" });
    if (payload.tenantId) headers.set("X-Tenant-Id", payload.tenantId);
    const url = new URL(`${controlPlaneUrl}${payload.path}`);
    void (async () => {
      try {
        const response = await fetch(url, {
          method: "POST",
          headers,
          body: JSON.stringify(payload.body ?? {}),
          signal: controller.signal,
        });
        if (!response.ok) {
          const detail: unknown = await response.json().catch(() => ({}));
          sender.send("audit:stream:end", { streamId: payload.streamId, error: readableControlPlaneError(detail) });
          return;
        }
        const reader = response.body?.getReader();
        if (!reader) throw new Error("empty response body");
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary: number;
          while ((boundary = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            let eventType = "message";
            let data = "";
            for (const line of block.split("\n")) {
              if (line.startsWith("event:")) eventType = line.slice("event:".length).trim();
              else if (line.startsWith("data:")) data += line.slice("data:".length).trim();
            }
            if (data) {
              try {
                sender.send("audit:stream:event", { streamId: payload.streamId, type: eventType, data: JSON.parse(data) });
              } catch {
                // skip a malformed SSE frame rather than killing the stream
              }
            }
          }
        }
        sender.send("audit:stream:end", { streamId: payload.streamId });
      } catch (error) {
        const aborted = error instanceof Error && error.name === "AbortError";
        sender.send("audit:stream:end", {
          streamId: payload.streamId,
          error: aborted ? "已取消。" : "无法连接本机控制平面。请先启动 Audit Network 服务后重试。",
        });
      } finally {
        activeStreams.delete(payload.streamId);
      }
    })();
  });
  ipcMain.on("audit:stream:cancel", (_event, payload: { streamId: string }) => {
    activeStreams.get(payload.streamId)?.abort();
    activeStreams.delete(payload.streamId);
  });
  ipcMain.handle("audit:selectKnowledgeFiles", async (event): Promise<KnowledgeSelection | null> => {
    assertTrustedSender(event);
    const result = await dialog.showOpenDialog({ properties: ["openFile", "multiSelections"] });
    return result.canceled ? null : createKnowledgeSelection(result.filePaths, "files");
  });
  ipcMain.handle("audit:selectKnowledgeFolder", async (event): Promise<KnowledgeSelection | null> => {
    assertTrustedSender(event);
    const result = await dialog.showOpenDialog({ properties: ["openDirectory"] });
    if (result.canceled || !result.filePaths[0]) return null;
    const root = resolve(result.filePaths[0]);
    return createKnowledgeSelection(await collectFolderKnowledgeFiles(root), "folder", root);
  });
  ipcMain.handle("audit:uploadKnowledge", (event, tenantId: string, selectionId: string) => {
    assertTrustedSender(event);
    return uploadKnowledge(tenantId, selectionId);
  });
  ipcMain.handle("audit:windowControl", (event, action: WindowControlAction) => {
    assertTrustedSender(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window) throw new Error("未找到请求操作的应用窗口。");
    if (action === "query") return { isMaximized: window.isMaximized() };
    if (action === "minimize") window.minimize();
    else if (action === "toggle-maximize") {
      if (window.isMaximized()) window.unmaximize();
      else window.maximize();
    } else if (action === "close") window.close();
    else throw new Error("拒绝未声明的窗口操作。");
    return { isMaximized: window.isMaximized() };
  });
  ipcMain.handle("audit:zoomControl", (event, action: "get" | "in" | "out" | "reset") => {
    assertTrustedSender(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window) throw new Error("未找到请求操作的应用窗口。");
    const contents = window.webContents;
    if (action === "get") return contents.getZoomFactor();
    if (action === "reset") {
      resetZoom(window);
      return 1;
    }
    applyZoomStep(window, action === "in" ? 0.1 : -0.1);
    return contents.getZoomFactor();
  });
  ipcMain.handle("audit:logList", (event): LogEntry[] => {
    assertTrustedSender(event);
    return [...unifiedLogs];
  });
  ipcMain.handle("audit:logClear", (event): void => {
    assertTrustedSender(event);
    unifiedLogs.length = 0;
  });
  ipcMain.handle("audit:logOpenWindow", (event): void => {
    assertTrustedSender(event);
    createLogWindow();
  });
  ipcMain.handle("audit:openAuditBrain", (event): { opened: boolean } => {
    assertTrustedSender(event);
    createAuditBrainWindow();
    return { opened: true };
  });
  ipcMain.handle("audit:openKnowledgeNebula", (event): { opened: boolean } => {
    assertTrustedSender(event);
    createKnowledgeNebulaWindow();
    return { opened: true };
  });
  unifiedLogDate = new Date().toISOString().slice(0, 10);
  unifiedLogPath = resolveUnifiedLogPath();
  installMainConsoleCapture();
  installLogFileWatcher();
  emitLog("system", "info", "==== Audit Network 统一日志会话启动 ====");
  createWindow(await loadSavedZoom());
  if (process.env.AUDIT_NETWORK_OPEN_BRAIN === "1") createAuditBrainWindow();
  if (process.env.AUDIT_NETWORK_OPEN_NEBULA === "1") createKnowledgeNebulaWindow();
  if (process.env.AUDIT_NETWORK_OPEN_FLOW === "1") createFlowCanvasWindow();
});

app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) void loadSavedZoom().then((zoom) => createWindow(zoom)); });
