/**
 * 主控台内嵌的可视化视图容器。
 *
 * 这些视图原本各自是一个独立的 Electron 窗口（审计插件大脑、知识星云、Run 画布、
 * 插件数据流全景），由 `electron/main.ts` 的 `createXxxWindow` 打开。收编后它们在主控台
 * 里以 iframe 承载，只保留一个窗口；原来的窗口函数与 `AUDIT_NETWORK_CAPTURE_*` 截图链路
 * 一并保留（自动化截图仍然依赖它们），只是主控台不再拿它当默认入口。
 *
 * 两条约束：
 *
 * 1. `src` 必须是**打包进应用的相对路径**（`./showcase/xxx.html`）。相对路径在开发
 *    （`ELECTRON_RENDERER_URL`）与生产（`file://.../renderer/index.html`）下都能解析，
 *    且天然排除了远程地址 —— 内嵌视图不接受外部 URL。
 * 2. iframe 内没有 preload，所以 `window.auditControl` 不存在。需要后端数据的视图
 *    （目前只有知识星云）经 postMessage 借用父窗口的 `auditControl`，桥在 `App.tsx`。
 *    这里刻意不开启 `nodeIntegrationInSubFrames` —— 那会把 preload 注入**每一个**子框架，
 *    与项目的最小权限基调相悖，而收益只是省掉一个三十行的桥。
 */
type ShowcaseFrameProps = {
  src: string;
  title: string;
  hint?: string;
};

export default function ShowcaseFrame({ src, title, hint }: ShowcaseFrameProps) {
  return (
    <section className="showcase-frame">
      <header className="showcase-bar">
        <span className="showcase-title">{title}</span>
        {hint ? <span className="showcase-hint">{hint}</span> : null}
        <span className="showcase-scope">内嵌视图 · 单窗口</span>
      </header>
      <iframe className="showcase-iframe" src={src} title={title} />
    </section>
  );
}
