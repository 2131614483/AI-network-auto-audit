import { Badge } from "antd";
import type { ReactNode } from "react";

/**
 * 所有页面共用的入参契约（由 App.tsx 在 renderView 时注入）。
 * - tenantId / tenantSlug：当前工作区；未连接时为 undefined，页面应自行禁用写操作。
 * - onNotice：把提示文案写进 App 顶部 notice 行。
 * - onNavigate：跨页深链跳转（?view=<page>&tab=<tab>）。
 * - tab：深链 ?tab=<tab>，用于 Tabs 页面定位初始标签。
 */
export interface PageProps {
  tenantId?: string;
  tenantSlug?: string;
  onNotice?: (message: string) => void;
  onNavigate?: (view: string, tab?: string) => void;
  tab?: string;
}

/** 只读请求的薄封装：与既有 window.auditControl.request 同一通道（主进程已注入租户/Trace）。 */
export function apiRequest<T = unknown>(options: {
  path: string;
  method?: "GET" | "POST" | "PUT";
  tenantId?: string;
  query?: Record<string, string | number | boolean | undefined>;
  body?: Record<string, unknown>;
}): Promise<T> {
  return window.auditControl.request(options) as Promise<T>;
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>;
}

/** 时间按 zh-CN 本地时区；空值显式占位，不留白。 */
export function fmtTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

/** 数字千分位。 */
export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN");
}

/** 哈希显示前 12 位，title 悬浮显示全量。 */
export function fmtHashShort(value: string | null | undefined, prefix = 12): string {
  if (!value) return "—";
  return value.length <= prefix ? value : `${value.slice(0, prefix)}…`;
}

export type HealthState = "ok" | "partial" | "broken";

/** 健康灯：已通（绿）/ 半通（黄）/ 断（红）。不假装正常。 */
export function HealthLamp({ state, label, hint }: { state: HealthState; label: string; hint?: ReactNode }) {
  const tone = state === "ok" ? "success" : state === "partial" ? "warning" : "error";
  return <Badge status={tone} text={<span title={typeof hint === "string" ? hint : undefined}>{label}{hint ? ` · ${hint}` : ""}</span>} />;
}

/** 统一空态文案：无数据必须显形，不允许留白。 */
export const EMPTY_NEVER = "从未产出";
export const EMPTY_UNLINKED = "未挂项目锚——archive_links 0 行";
export const EMPTY_NO_BUNDLE = "无证据包（可能已被清理）";
export const EMPTY_UNWIRED = "未接线";
