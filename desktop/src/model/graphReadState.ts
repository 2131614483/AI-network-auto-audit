export type GraphReadState<T> = {
  status: "idle" | "loading" | "ready" | "error";
  request: number;
  tenant?: string;
  proposals: T[];
  message?: string;
};

type Action<T> =
  | { type: "start"; request: number; tenant: string }
  | { type: "reset"; request: number }
  | { type: "success"; request: number; proposals: T[] }
  | { type: "failure"; request: number; message: string };

export const initialGraphReadState: GraphReadState<never> = { status: "idle", request: 0, proposals: [] };

export function graphProposalsFromResponse<T>(payload: unknown): T[] {
  if (!payload || typeof payload !== "object" || !Array.isArray((payload as { proposals?: unknown }).proposals)) {
    throw new Error("抽取提案响应格式不正确");
  }
  return (payload as { proposals: T[] }).proposals;
}

export function graphReadTransition<T>(state: GraphReadState<T>, action: Action<T>): GraphReadState<T> {
  if (action.type === "start") return { status: "loading", request: action.request, tenant: action.tenant, proposals: [] };
  if (action.type === "reset") return { status: "idle", request: action.request, proposals: [] };
  if (action.request !== state.request) return state;
  if (action.type === "success") return { status: "ready", request: action.request, tenant: state.tenant, proposals: action.proposals };
  return { status: "error", request: action.request, tenant: state.tenant, proposals: [], message: action.message };
}
