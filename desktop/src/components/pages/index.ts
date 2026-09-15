import { createElement, type ComponentType } from "react";
import type { PageProps } from "./common";
import ComingSoonPage from "./ComingSoonPage";
export type { PageProps } from "./common";
import DiagnosisPage from "./DiagnosePage";
import EvolutionPage from "./EvolutionPage";
import GraphExtractPage from "./GraphExtractPage";
import HubPage from "./HubPage";
import KnowledgePage from "./KnowledgePage";
import CasesPage from "./CasesPage";
import LibraryPage from "./LibraryPage";
import PluginCatalogPage from "./PluginCatalogPage";
import PluginLifecyclePage from "./PluginLifecyclePage";
import PluginsPage from "./PluginsPage";
import RulesPage from "./RulesPage";
import RunsPage from "./RunsPage";
import SearchPage from "./SearchPage";
import OperationsPage from "./OperationsPage";
import SchedulesPage from "./SchedulesPage";
import KnowledgeSearchPage from "./KnowledgeSearchPage";
import KnowledgePendingPage from "./KnowledgePendingPage";
import KnowledgeRecyclePage from "./KnowledgeRecyclePage";
import LineagePage from "./LineagePage";
import GraphSpacesPage from "./GraphSpacesPage";
import GraphMergePage from "./GraphMergePage";
import ConnectivityPage from "./ConnectivityPage";
import EvidencePage from "./EvidencePage";
import ExperiencePage from "./ExperiencePage";
import HealthPage from "./HealthPage";
import PlanningPage from "./PlanningPage";
import PolicyPage from "./PolicyPage";
import PublicationsPage from "./PublicationsPage";
import SuggestionsPage from "./SuggestionsPage";

/**
 * 页面注册表：view key → 页面组件。
 * 新增页面只改这里，不再动 App.tsx 的渲染逻辑。
 * 未在本批实现的 P1/P2 页面与 search/library/cases 统一渲染 ComingSoonPage（菜单保持可见可点）。
 */
const comingSoon: ComponentType<PageProps> = () => createElement(ComingSoonPage);

export const pageRegistry: Record<string, ComponentType<PageProps>> = {
  // 组 1 · 总览
  hub: HubPage,
  search: SearchPage,
  // 组 2 · 运行
  runs: RunsPage,
  diagnose: DiagnosisPage,
  operations: OperationsPage,
  schedules: SchedulesPage,
  // 组 3 · 知识与文档
  knowledge: KnowledgePage,
  "knowledge-search": KnowledgeSearchPage,
  "knowledge-pending": KnowledgePendingPage,
  "knowledge-recycle": KnowledgeRecyclePage,
  library: LibraryPage,
  rules: RulesPage,
  // 组 4 · 图谱
  "graph-extract": GraphExtractPage,
  "graph-spaces": GraphSpacesPage,
  "graph-merge": GraphMergePage,
  lineage: LineagePage,
  // 组 5 · 插件与组网
  plugins: PluginsPage,
  "plugin-catalog": PluginCatalogPage,
  "plugin-lifecycle": PluginLifecyclePage,
  connectivity: ConnectivityPage,
  planning: PlanningPage,
  // 组 6 · 业务与案例
  cases: CasesPage,
  // 组 7 · 经验与治理
  experience: ExperiencePage,
  suggestions: SuggestionsPage,
  evolution: EvolutionPage,
  publications: PublicationsPage,
  evidence: EvidencePage,
  policy: PolicyPage,
  // 组 8 · 系统
  health: HealthPage,
};
