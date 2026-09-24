import { Button, Result, Space } from "antd";
import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * 页面级错误边界：只包住 `renderView()` 渲染出的页面，不包外壳（顶栏 / 侧栏）。
 *
 * 为什么需要它：此前 React 树里没有任何错误边界，任何一个页面抛出的未捕获异常都会让
 * React 卸载**整棵树** —— 用户看到的是整片空白窗口，顶栏与 42 个导航项一起消失，连
 * "哪里出错了"都读不到。Run 画布的平移竞态就是这样把整个控制台打崩的（见
 * `docs/进化闭环断点-根因与修复方案-20260923.md` §8）。
 *
 * 边界把故障限制在当前页：外壳保持可用，用户能切到别的页面继续工作，并且能看到
 * 真实的错误信息，而不是一个黑屏。
 */
export default class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 留痕：主进程的渲染器日志捕获会一并收走，便于事后定位。
    console.error("页面渲染失败（已隔离，控制台外壳未受影响）", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Result
        status="error"
        title="此页面渲染失败，控制台其余部分仍可用"
        subTitle={error.message || String(error)}
        extra={
          <Space>
            <Button type="primary" onClick={() => this.setState({ error: null })}>重试</Button>
            <Button onClick={() => window.location.reload()}>重新加载控制台</Button>
          </Space>
        }
      />
    );
  }
}
