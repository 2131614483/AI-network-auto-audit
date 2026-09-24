// 必须最先导入：antd v5 的静态方法（`message.*` / `notification.*` / `Modal.*`）在
// React 19 下会**静默失效**——不报错，只是什么都不弹。本项目有 7 处 `message.*`
// 调用（其中 3 处是"复验失败 / 搜索失败"的错误提示），用户点了操作、失败了却看不到
// 任何反馈。这个官方补丁在 antd 被使用之前接管这些静态方法。
// 注意：导入顺序不能下调，`./App` 与 `antd/dist/reset.css` 都会先用到 antd。
import "@ant-design/v5-patch-for-react-19";
import React from "react";
import ReactDOM from "react-dom/client";
import "antd/dist/reset.css";
import "./styles/globals.css";
import App from "./App";

// 阻止 Ctrl+滚轮 / 触控板捏合触发 Chromium 页面缩放（捏合在 Chromium 中即 ctrl+wheel）。
// 只在按住 Ctrl 时 preventDefault，普通滚轮滚动不受任何影响；界面缩放请用工具栏按钮
// 或视图菜单（Ctrl+= / Ctrl+- / Ctrl+0）。不能在主进程用 setVisualZoomLevelLimits(1,1)
// 实现同样目的——该调用存在导致滚轮完全无法滚动页面的已知问题。
window.addEventListener(
  "wheel",
  (event) => {
    if (event.ctrlKey) event.preventDefault();
  },
  { passive: false },
);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
