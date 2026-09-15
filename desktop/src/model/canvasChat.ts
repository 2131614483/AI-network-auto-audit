// CW5 canvas chat model: message types for the AI chat panel and the
// draft <-> canvas projection conversion used by "apply to canvas".

export type ChatDraftPort = {
  port_id: string;
  direction: "input" | "output";
  schema_ref?: string;
  schema_version?: string;
  schema_sha256?: string;
  media_type?: string;
  required?: boolean;
  cardinality?: string;
  classification?: string;
  transport?: string;
};

export type ChatDraftNode = {
  node_instance_id: string;
  capability: string;
  plugin_id: string;
  input_ports: ChatDraftPort[];
  output_ports: ChatDraftPort[];
};

export type ChatDraftEdge = {
  edge_id: string;
  source_instance: string;
  source_port: string;
  target_instance: string;
  target_port: string;
  adapter?: string | null;
};

export type ChatDraft = {
  plan_key: string;
  nodes: ChatDraftNode[];
  edges: ChatDraftEdge[];
  budget?: Record<string, number>;
  seed_inputs?: Array<[string, string]>;
  selection_reasons?: string;
};

export type ChatIssue = {
  code: string;
  message?: string;
  node_id?: string | null;
  port_id?: string | null;
  edge_id?: string | null;
};

export type ChatMessageKind = "text" | "draft" | "gap" | "error";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  kind: ChatMessageKind;
  planKey?: string;
  revisions?: number;
  draft?: ChatDraft | null;
  issues?: ChatIssue[];
  intentId?: string | null;
  createdAt: string;
};

export type CanvasChatResponse = {
  status: "draft_ready" | "gap_report";
  plan_key: string;
  revisions: number;
  draft: ChatDraft | null;
  issues: ChatIssue[];
  session_id: string;
  reply_text: string;
  intent_id: string | null;
  backend: string;
};

export function chatTurnText(message: ChatMessage): string {
  return message.content;
}

export function seedInputsOf(draft: ChatDraft | null | undefined): Array<[string, string]> {
  return (draft?.seed_inputs ?? []).map((pair) => [String(pair[0]), String(pair[1])]);
}
