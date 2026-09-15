import { ToolOutlined } from "@ant-design/icons";
import { Card, Empty, Typography } from "antd";
import type { PageProps } from "./common";

/**
 * 未实现页面（P1/P2 + search/library/cases）的统一占位。
 * 菜单项保持可见、可点，不 disabled——先把信息架构铺出来，内容后补。
 */
export default function ComingSoonPage(_props: PageProps) {
  return (
    <Card className="chart-card" title={<span><ToolOutlined /> 该页面</span>}>
      <Empty
        description={
          <Typography.Text>
            <Typography.Title level={5}>即将上线 / 开发中</Typography.Title>
            <Typography.Paragraph type="secondary">
              信息架构 v2 已在此处留位（两级导航 P1/P2 批次）。本页端点与字段详设见设计文档 §13.3，
              当前批次先交付 P0 页面骨架，本页数据接入在后续批次补齐。
            </Typography.Paragraph>
          </Typography.Text>
        }
      />
    </Card>
  );
}
