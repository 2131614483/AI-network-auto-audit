interface Window {
  auditControl: {
    request(request: {
      path: string;
      method?: "GET" | "POST" | "PUT";
      tenantId?: string;
      tenantSlug?: string;
      body?: Record<string, unknown>;
      query?: Record<string, string | number | boolean | undefined>;
    }): Promise<unknown>;
    requestStream(options: {
      path: string;
      tenantId?: string;
      body?: Record<string, unknown>;
      onEvent: (event: { type: string; data: unknown }) => void;
      onEnd: () => void;
      onError: (error: Error) => void;
    }): () => void;
    runsSnapshot(tenantId: string): Promise<{
      tenantId: string;
      items: Array<Record<string, unknown>>;
      traceId: string;
      updatedAt: number;
      offline: boolean;
    }>;
    selectKnowledgeFiles(): Promise<KnowledgeSelection | null>;
    selectKnowledgeFolder(): Promise<KnowledgeSelection | null>;
    uploadKnowledge(tenantId: string, selectionId: string): Promise<unknown>;
    windowControl(action: "minimize" | "toggle-maximize" | "close" | "query"): Promise<{ isMaximized: boolean }>;
    zoomControl(action: "get" | "in" | "out" | "reset"): Promise<number>;
    logList(): Promise<LogEntry[]>;
    logClear(): Promise<void>;
    logOpenWindow(): Promise<void>;
    openAuditBrain(): Promise<{ opened: boolean }>;
    openKnowledgeNebula(): Promise<{ opened: boolean }>;
    logOnEvent(callback: (entry: LogEntry) => void): () => void;
  };
}

interface LogEntry {
  id: number;
  ts: string;
  source: "main" | "renderer" | "api" | "worker" | "system";
  level: "verbose" | "info" | "warning" | "error";
  message: string;
}

interface KnowledgeSelection {
  selectionId: string;
  sourceKind: "files" | "folder";
  files: Array<{ relativePath: string; sizeBytes: number }>;
}
