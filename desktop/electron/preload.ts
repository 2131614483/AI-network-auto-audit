import { contextBridge, ipcRenderer } from "electron";

type SafeRequest = {
  path: string;
  method?: "GET" | "POST" | "PUT";
  tenantId?: string;
  tenantSlug?: string;
  body?: Record<string, unknown>;
  query?: Record<string, string | number | boolean | undefined>;
};

type WindowControlAction = "minimize" | "toggle-maximize" | "close" | "query";
type ZoomControlAction = "get" | "in" | "out" | "reset";
type KnowledgeSelection = {
  selectionId: string;
  sourceKind: "files" | "folder";
  files: Array<{ relativePath: string; sizeBytes: number }>;
};
type LogEntry = { id: number; ts: string; source: string; level: string; message: string };

function normaliseIpcError(error: unknown): Error {
  const message = error instanceof Error ? error.message : "本机控制平面请求失败。";
  return new Error(message.replace(/^Error invoking remote method 'audit:[^']+': Error: /, ""));
}

contextBridge.exposeInMainWorld("auditControl", {
  request: async (request: SafeRequest): Promise<unknown> => {
    try { return await ipcRenderer.invoke("audit:request", request); }
    catch (error) { throw normaliseIpcError(error); }
  },
  requestStream: (options: {
    path: string;
    tenantId?: string;
    body?: Record<string, unknown>;
    onEvent: (event: { type: string; data: unknown }) => void;
    onEnd: () => void;
    onError: (error: Error) => void;
  }): (() => void) => {
    const streamId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const listener = (_event: unknown, payload: { streamId: string; type: string; data: unknown }) => {
      if (payload.streamId !== streamId) return;
      options.onEvent({ type: payload.type, data: payload.data });
    };
    const endListener = (_event: unknown, payload: { streamId: string; error?: string }) => {
      if (payload.streamId !== streamId) return;
      if (payload.error) options.onError(new Error(payload.error));
      else options.onEnd();
    };
    ipcRenderer.on("audit:stream:event", listener as (event: unknown, ...args: unknown[]) => void);
    ipcRenderer.on("audit:stream:end", endListener as (event: unknown, ...args: unknown[]) => void);
    ipcRenderer.send("audit:stream:start", {
      streamId,
      path: options.path,
      tenantId: options.tenantId,
      body: options.body,
    });
    return () => {
      ipcRenderer.removeListener("audit:stream:event", listener as (event: unknown, ...args: unknown[]) => void);
      ipcRenderer.removeListener("audit:stream:end", endListener as (event: unknown, ...args: unknown[]) => void);
      ipcRenderer.send("audit:stream:cancel", { streamId });
    };
  },
  runsSnapshot: (tenantId: string): Promise<unknown> => ipcRenderer.invoke("audit:runsSnapshot", tenantId),
  selectKnowledgeFiles: (): Promise<KnowledgeSelection | null> => ipcRenderer.invoke("audit:selectKnowledgeFiles"),
  selectKnowledgeFolder: (): Promise<KnowledgeSelection | null> => ipcRenderer.invoke("audit:selectKnowledgeFolder"),
  uploadKnowledge: async (tenantId: string, selectionId: string): Promise<unknown> => {
    try { return await ipcRenderer.invoke("audit:uploadKnowledge", tenantId, selectionId); }
    catch (error) { throw normaliseIpcError(error); }
  },
  windowControl: async (action: WindowControlAction): Promise<{ isMaximized: boolean }> => {
    try { return await ipcRenderer.invoke("audit:windowControl", action); }
    catch (error) { throw normaliseIpcError(error); }
  },
  zoomControl: async (action: ZoomControlAction): Promise<number> => {
    try { return await ipcRenderer.invoke("audit:zoomControl", action); }
    catch (error) { throw normaliseIpcError(error); }
  },
  logList: (): Promise<LogEntry[]> => ipcRenderer.invoke("audit:logList"),
  logClear: (): Promise<void> => ipcRenderer.invoke("audit:logClear"),
  logOpenWindow: (): Promise<void> => ipcRenderer.invoke("audit:logOpenWindow"),
  openAuditBrain: (): Promise<{ opened: boolean }> => ipcRenderer.invoke("audit:openAuditBrain"),
  openKnowledgeNebula: (): Promise<{ opened: boolean }> => ipcRenderer.invoke("audit:openKnowledgeNebula"),
  logOnEvent: (callback: (entry: LogEntry) => void): (() => void) => {
    const listener = (_event: unknown, entry: LogEntry) => callback(entry);
    ipcRenderer.on("audit:logEvent", listener as (event: unknown, ...args: unknown[]) => void);
    return () => { ipcRenderer.removeListener("audit:logEvent", listener as (event: unknown, ...args: unknown[]) => void); };
  },
});
