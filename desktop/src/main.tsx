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
