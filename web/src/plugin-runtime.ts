export const STABLE_SLOTS = [
  "global.navigation",
  "workspace.navigation",
  "workspace.tools",
  "dashboard.cards",
  "entity.tabs",
  "detail.actions",
  "settings.sections",
] as const;

export type StableSlot = (typeof STABLE_SLOTS)[number];

export interface UIAction {
  id: string;
  capability: string;
  label_key: string;
  argument_schema: Record<string, unknown>;
  placement: string[];
}

export interface UIContribution {
  schema_version: string;
  navigation: Array<{ slot: string; title_key: string; route: string; icon?: string }>;
  views: Array<{ id: string; renderer: string; data_source: string }>;
  actions: UIAction[];
  i18n: string[];
}

export function isSupportedSlot(slot: string): slot is StableSlot {
  return (STABLE_SLOTS as readonly string[]).includes(slot);
}

export function normalizeContribution(input: UIContribution, supportedMajor = 1): UIContribution {
  const major = Number.parseInt(input.schema_version.split(".")[0] ?? "0", 10);
  if (major !== supportedMajor) {
    throw new Error(`unsupported UI schema major version: ${input.schema_version}`);
  }
  return {
    ...input,
    navigation: input.navigation.filter((entry) => isSupportedSlot(entry.slot)),
    actions: input.actions.map((action) => ({ ...action, placement: [...action.placement] })),
  };
}

export function actionRequest(action: UIAction, args: Record<string, unknown>) {
  return { capability: action.capability, arguments: args, action_id: action.id };
}
