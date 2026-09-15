import { FileSearchOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Input, Modal, Progress, Select, Space, Table, Tabs, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, asRecord, type PageProps } from "./common";

type KnowledgeStats = { documents: number; chunks: number; embedding_chunks: number; embedding_model: string | null; embedding_coverage: number; embedding_models: Record<string, number>; waiting_extractor: number; failed_files: number; vector_ready: boolean };
type KnowledgeDocument = { id: string; title: string; source_uri: string; status: string; version: number; chunks: number; embedding_chunks: number; embedding_status: string; updated_at: string };
type KnowledgeBatch = { id: string; source_uri: string; status: string; scanned: number; accepted: number; skipped: number; failed: number; deferred: number; created_at: string };
type KnowledgeHit = { chunk_id: string; title: string; source_uri: string; ordinal: number; content: string; score: number; retrieval_mode: string; matched_by: string[] };
type KnowledgeSelection = { selectionId: string; sourceKind: "files" | "folder"; files: Array<{ relativePath: string; sizeBytes: number }> };
type KnowledgeRecycleItem = { id: string; entity_id: string; title: string | null; source_uri: string | null; prior_status: string; reason: string; deleted_at: string; restored_at: string | null };
type KnowledgeAdapter = { key: string; label: string; supported_suffixes: string[]; status: string; execution_mode: string; next_step: string };
type KnowledgePendingFile = { id: string; relative_path: string; source_uri: string; mime_type: string; size_bytes: number; status: "waiting_extractor" | "queued" | "processing" | "failed"; error_detail?: string | null; created_at: string | null };

/**
 * 文档工作台（原 App.tsx knowledge 视图提级）。
 * 只搬运不重写：端点 /knowledge/{stats,documents,batches,adapters,recycle-bin,rich-media/pending,search,embed} 与全部写操作保持原样。
 */
export default function KnowledgePage({ tenantId, onNotice, tab }: PageProps) {
  const [modal] = Modal.useModal();
  const [busy, setBusy] = useState(false);
  const [knowledgeStats, setKnowledgeStats] = useState<KnowledgeStats>();
  const [knowledgeDocuments, setKnowledgeDocuments] = useState<KnowledgeDocument[]>([]);
  const [knowledgeBatches, setKnowledgeBatches] = useState<KnowledgeBatch[]>([]);
  const [knowledgeRecycleBin, setKnowledgeRecycleBin] = useState<KnowledgeRecycleItem[]>([]);
  const [knowledgeAdapters, setKnowledgeAdapters] = useState<KnowledgeAdapter[]>([]);
  const [knowledgePending, setKnowledgePending] = useState<KnowledgePendingFile[]>([]);
  const [knowledgeHits, setKnowledgeHits] = useState<KnowledgeHit[]>([]);
  const [searchMode, setSearchMode] = useState("hybrid");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchDegraded, setSearchDegraded] = useState(false);
  const [selectedKnowledge, setSelectedKnowledge] = useState<KnowledgeSelection>();
  const [importResult, setImportResult] = useState<unknown>();

  const load = useCallback(async (id: string) => {
    const [statsPayload, documentsPayload, batchesPayload, adaptersPayload] = await Promise.all([
      apiRequest({ path: "/api/v1/knowledge/stats", tenantId: id }),
      apiRequest({ path: "/api/v1/knowledge/documents", tenantId: id }),
      apiRequest({ path: "/api/v1/knowledge/batches", tenantId: id }),
      apiRequest({ path: "/api/v1/knowledge/adapters", tenantId: id }),
    ]);
    setKnowledgeStats(statsPayload as KnowledgeStats);
    setKnowledgeDocuments((asRecord(documentsPayload).items as KnowledgeDocument[]) ?? []);
    setKnowledgeBatches((asRecord(batchesPayload).items as KnowledgeBatch[]) ?? []);
    setKnowledgeAdapters((asRecord(adaptersPayload).items as KnowledgeAdapter[]) ?? []);
  }, []);

  const loadRecycle = useCallback(async (id: string) => {
    const payload = asRecord(await apiRequest({ path: "/api/v1/knowledge/recycle-bin", tenantId: id }));
    setKnowledgeRecycleBin((payload.items as KnowledgeRecycleItem[]) ?? []);
  }, []);

  const loadPending = useCallback(async (id: string) => {
    const payload = asRecord(await apiRequest({ path: "/api/v1/knowledge/rich-media/pending", tenantId: id }));
    setKnowledgePending((payload.items as KnowledgePendingFile[]) ?? []);
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const chooseFiles = async (): Promise<void> => {
    const selection = await window.auditControl.selectKnowledgeFiles();
    setSelectedKnowledge(selection ?? undefined); setImportResult(undefined);
  };
  const chooseFolder = async (): Promise<void> => {
    const selection = await window.auditControl.selectKnowledgeFolder();
    setSelectedKnowledge(selection ?? undefined); setImportResult(undefined);
  };
  const importKnowledge = async (): Promise<void> => {
    if (!tenantId || !selectedKnowledge) return;
    setBusy(true);
    try {
      const result = await window.auditControl.uploadKnowledge(tenantId, selectedKnowledge.selectionId);
      setImportResult(result); await load(tenantId); onNotice?.("知识任务已提交至策略网关与本地处理队列。");
    } catch (error) { onNotice?.(error instanceof Error ? error.message : "知识导入失败。"); }
    finally { setBusy(false); }
  };
  const retireDocument = (document: KnowledgeDocument): void => {
    if (!tenantId) return;
    modal.confirm({
      title: "移入知识回收站？",
      content: `“${document.title}”会从知识库和检索结果中隐藏，原始记录与版本会保留，可随时恢复。`,
      okText: "移入回收站", okButtonProps: { danger: true }, cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await apiRequest({ path: `/api/v1/knowledge/documents/${document.id}/retire`, method: "POST", tenantId, body: { reason: "桌面端用户移入知识回收站" } });
          await Promise.all([load(tenantId), loadRecycle(tenantId)]);
          onNotice?.("文档已移入回收站；历史内容没有被硬删除。");
        } catch (error) { onNotice?.(error instanceof Error ? error.message : "移入知识回收站失败。"); }
        finally { setBusy(false); }
      },
    });
  };
  const restoreDocument = (item: KnowledgeRecycleItem): void => {
    if (!tenantId) return;
    modal.confirm({
      title: "恢复知识文档？",
      content: `“${item.title ?? "未命名文档"}”将恢复到知识库和检索范围，保留原来的版本与来源。`,
      okText: "恢复文档", cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await apiRequest({ path: `/api/v1/knowledge/recycle-bin/${item.id}/restore`, method: "POST", tenantId, body: { reason: "桌面端用户恢复知识文档" } });
          await Promise.all([load(tenantId), loadRecycle(tenantId)]);
          onNotice?.("文档已恢复到知识库和检索范围。");
        } catch (error) { onNotice?.(error instanceof Error ? error.message : "恢复知识文档失败。"); }
        finally { setBusy(false); }
      },
    });
  };
  const extractRichMedia = (file: KnowledgePendingFile, retry = false): void => {
    if (!tenantId) return;
    modal.confirm({
      title: retry ? "重新提交本地解析？" : "提交本地解析？",
      content: retry
        ? `“${file.relative_path}”上次解析失败。确认后会创建一次新的、经过策略网关审核的本地解析任务。`
        : `“${file.relative_path}”将进入本地 MinerU 队列，解析为带页码引用的文本块。Worker 会一次只处理一个文件，避免争用本机算力。`,
      okText: retry ? "重新提交" : "提交队列", cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await apiRequest({ path: retry ? "/api/v1/knowledge/rich-media/retry" : "/api/v1/knowledge/rich-media/extract", method: "POST", tenantId, body: { ingest_file_id: file.id, method: "auto" } });
          await Promise.all([load(tenantId), loadPending(tenantId)]);
          onNotice?.(retry ? "失败文件已重新进入本地解析队列。" : "文件已进入本地 MinerU 队列，完成后会自动写入页码引用。");
        } catch (error) { onNotice?.(error instanceof Error ? error.message : "本地解析任务提交失败。"); }
        finally { setBusy(false); }
      },
    });
  };
  const searchKnowledge = async (): Promise<void> => {
    if (!tenantId || !searchQuery.trim()) return;
    setBusy(true);
    try {
      const payload = asRecord(await apiRequest({ path: "/api/v1/knowledge/search", method: "POST", tenantId, body: { query: searchQuery.trim(), mode: searchMode, limit: 20 } }));
      setKnowledgeHits((payload.items as KnowledgeHit[]) ?? []);
      setSearchDegraded(Boolean(payload.degraded));
      onNotice?.(Boolean(payload.degraded) ? "向量服务不可用，本次已降级为全文检索。" : "知识检索完成，结果保留来源与分块定位。");
    } catch (error) { onNotice?.(error instanceof Error ? error.message : "知识检索失败。"); }
    finally { setBusy(false); }
  };
  const embedNextBatch = async (): Promise<void> => {
    if (!tenantId) return;
    setBusy(true);
    try {
      const payload = asRecord(await apiRequest({ path: "/api/v1/knowledge/embed", method: "POST", tenantId, body: { limit: 16 } }));
      await load(tenantId);
      onNotice?.(`本地向量化完成：${String(payload.embedded)} 个知识块，模型 ${String(payload.model)}。`);
    } catch (error) { onNotice?.(error instanceof Error ? error.message : "本地向量化失败。"); }
    finally { setBusy(false); }
  };

  return <>
    <section className="data-bar knowledge-metrics">
      <Card className="metric-card" size="small"><span>文档</span><strong>{knowledgeStats?.documents ?? "—"}</strong><small>当前租户</small></Card>
      <Card className="metric-card" size="small"><span>知识块</span><strong>{knowledgeStats?.chunks ?? "—"}</strong><small>当前版本</small></Card>
      <Card className="metric-card" size="small"><span>已向量化</span><strong>{knowledgeStats?.embedding_chunks ?? "—"}</strong><small>{knowledgeStats ? `${knowledgeStats.embedding_model ?? "模型未配置"} · 覆盖 ${((knowledgeStats.embedding_coverage ?? 0) * 100).toFixed(1)}%` : "模型待运行"}</small></Card>
      <Card className="metric-card" size="small"><span>待本地解析</span><strong>{knowledgeStats?.waiting_extractor ?? "—"}</strong><small>PDF / 图片 / 音视频</small></Card>
    </section>
    <Card className="chart-card" title="知识工厂" extra={<Space><Tag className={(knowledgeStats?.vector_ready && (knowledgeStats?.embedding_coverage ?? 0) >= 0.95) ? "ready-tag" : "pending-tag"}>{!knowledgeStats?.vector_ready ? "全文降级" : (knowledgeStats.embedding_coverage ?? 0) >= 0.95 ? "向量可用" : `部分已向量化 ${((knowledgeStats.embedding_coverage ?? 0) * 100).toFixed(1)}%`}</Tag><Button size="small" loading={busy} onClick={() => tenantId && void load(tenantId)}>刷新</Button></Space>}>
      <Tabs defaultActiveKey={tab ?? "import"} items={[
        {
          key: "import", label: "文件导入", children: <div className="knowledge-pane">
            <Typography.Paragraph>选择文件或文件夹后，系统会保留文件夹内的相对路径。文本、CSV 与 Office 文件在本机解析；PDF/图片等待本地 MinerU，音视频等待本地转写。所有写入均经过策略网关。</Typography.Paragraph>
            <Space wrap>
              <Button icon={<FileSearchOutlined />} onClick={() => void chooseFiles()}>选择文件</Button>
              <Button icon={<FileSearchOutlined />} onClick={() => void chooseFolder()}>选择文件夹</Button>
              <Button type="primary" disabled={!tenantId || !selectedKnowledge} loading={busy} onClick={() => void importKnowledge()}>通过策略网关导入</Button>
              <Button loading={busy} onClick={() => void embedNextBatch()}>生成下一批向量</Button>
            </Space>
            <Descriptions className="file-summary" column={1} size="small" bordered items={[
              { key: "source", label: "选择来源", children: selectedKnowledge ? (selectedKnowledge.sourceKind === "folder" ? "文件夹" : "单独文件") : "尚未选择" },
              { key: "files", label: "已选文件", children: selectedKnowledge ? `${selectedKnowledge.files.length} 个：${selectedKnowledge.files.slice(0, 8).map((file) => file.relativePath).join("、")}${selectedKnowledge.files.length > 8 ? " …" : ""}` : "尚未选择" },
            ]} />
            {importResult ? <pre className="result-box">{JSON.stringify(importResult, null, 2)}</pre> : null}
          </div>,
        },
        {
          key: "documents", label: "文档管理", children: <Table className="stock-table" size="small" rowKey="id" dataSource={knowledgeDocuments} pagination={{ pageSize: 12 }} expandable={{ expandedRowRender: (row) => <Descriptions className="nested-descriptions" size="small" column={2} bordered items={[
            { key: "id", label: "文档 ID", children: row.id },
            { key: "source", label: "来源 URI", children: row.source_uri },
            { key: "version", label: "版本", children: row.version },
            { key: "chunks", label: "知识块", children: row.chunks },
            { key: "vector", label: "已向量化", children: `${row.embedding_chunks}/${row.chunks}` },
            { key: "vector_status", label: "向量状态", children: <Tag className={row.embedding_status === "ready" ? "ready-tag" : "pending-tag"}>{row.embedding_status}</Tag> },
            { key: "updated", label: "更新时间", children: new Date(row.updated_at).toLocaleString("zh-CN") },
          ]} /> }} columns={[
            { title: "文档", dataIndex: "title", key: "title" },
            { title: "版本", dataIndex: "version", key: "version" },
            { title: "分块", dataIndex: "chunks", key: "chunks" },
            { title: "向量", key: "embedding", render: (_value, row) => <Tag className={row.embedding_status === "ready" ? "ready-tag" : "pending-tag"}>{row.embedding_chunks}/{row.chunks}</Tag> },
            { title: "状态", dataIndex: "status", key: "status" },
            { title: "来源", dataIndex: "source_uri", key: "source_uri", ellipsis: true },
            { title: "操作", key: "actions", render: (_value, row) => <Button size="small" danger disabled={!tenantId || busy} onClick={() => retireDocument(row)}>移入回收站</Button> },
          ]} locale={{ emptyText: "暂无知识文档" }} />,
        },
        { key: "batches", label: "处理批次", children: <Table className="stock-table" size="small" rowKey="id" dataSource={knowledgeBatches} pagination={{ pageSize: 10 }} columns={[{ title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },{ title: "状态", dataIndex: "status", key: "status" },{ title: "进度", key: "progress", render: (_value, row) => { const done = row.accepted + row.skipped + row.failed + row.deferred; const percent = row.scanned ? Math.min(100, Math.round(done * 100 / row.scanned)) : 0; return <Progress percent={percent} size="small" status={row.failed ? "exception" : undefined} />; } },{ title: "接收/跳过/待解析", key: "counts", render: (_value, row) => `${row.accepted} / ${row.skipped} / ${row.deferred}` },{ title: "来源", dataIndex: "source_uri", key: "source_uri", ellipsis: true }]} locale={{ emptyText: "暂无处理批次" }} /> },
        {
          key: "pending", label: "本地解析队列", children: <Table className="stock-table" size="small" rowKey="id" dataSource={knowledgePending} pagination={{ pageSize: 12 }} columns={[
            { title: "文件", dataIndex: "relative_path", key: "relative_path", ellipsis: true },
            { title: "类型", dataIndex: "mime_type", key: "mime_type", render: (value: string) => value.replace("application/", "") },
            { title: "大小", key: "size", render: (_value, row) => `${(row.size_bytes / 1024).toFixed(1)} KB` },
            { title: "状态", dataIndex: "status", key: "status", render: (value: KnowledgePendingFile["status"]) => <Tag className={value === "failed" ? "pending-tag" : value === "processing" ? "gateway-tag" : "ready-tag"}>{value === "waiting_extractor" ? "待提交" : value === "queued" ? "排队中" : value === "processing" ? "解析中" : "解析失败"}</Tag> },
            { title: "说明", dataIndex: "error_detail", key: "error_detail", ellipsis: true, render: (value: string | null | undefined) => value ?? "—" },
            { title: "操作", key: "actions", render: (_value, row) => row.status === "failed" ? <Button size="small" danger disabled={!tenantId || busy} onClick={() => extractRichMedia(row, true)}>重新提交</Button> : <Button size="small" type="primary" disabled={!tenantId || busy || row.status !== "waiting_extractor"} onClick={() => extractRichMedia(row)}>提交队列</Button> },
          ]} locale={{ emptyText: "暂无待处理的 PDF / 图片文件。" }} />,
        },
        {
          key: "recycle", label: "知识回收站", children: <Table className="stock-table" size="small" rowKey="id" dataSource={knowledgeRecycleBin} pagination={{ pageSize: 12 }} columns={[
            { title: "文档", dataIndex: "title", key: "title", render: (value: string | null) => value ?? "未命名文档" },
            { title: "原状态", dataIndex: "prior_status", key: "prior_status" },
            { title: "移入时间", dataIndex: "deleted_at", key: "deleted_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
            { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true },
            { title: "来源", dataIndex: "source_uri", key: "source_uri", ellipsis: true },
            { title: "操作", key: "actions", render: (_value, row) => <Button size="small" type="primary" disabled={!tenantId || busy} onClick={() => restoreDocument(row)}>恢复文档</Button> },
          ]} locale={{ emptyText: "回收站为空；不会显示或处理已经恢复的文档。" }} />,
        },
        {
          key: "adapters", label: "本地适配器", children: <Table className="stock-table" size="small" rowKey="key" dataSource={knowledgeAdapters} pagination={false} columns={[
            { title: "接口", dataIndex: "label", key: "label" },
            { title: "支持格式", dataIndex: "supported_suffixes", key: "supported_suffixes", render: (value: string[]) => value.join(" · ") },
            { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "available" ? "ready-tag" : "pending-tag"}>{value === "available" ? "可用" : "等待本地运行时"}</Tag> },
            { title: "执行方式", dataIndex: "execution_mode", key: "execution_mode", render: (value: string) => value === "isolated_subprocess" ? "隔离子进程" : "未配置，不执行" },
            { title: "下一步", dataIndex: "next_step", key: "next_step", ellipsis: true },
          ]} locale={{ emptyText: "未声明本地适配器。" }} />,
        },
        { key: "search", label: "混合检索", children: <div className="knowledge-pane"><Space.Compact className="knowledge-search"><Select value={searchMode} onChange={setSearchMode} options={[{ value: "hybrid", label: "混合检索" },{ value: "keyword", label: "全文检索" },{ value: "vector", label: "向量检索" }]} /><Input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onPressEnter={() => void searchKnowledge()} placeholder="输入审计问题、制度或实体" /><Button type="primary" loading={busy} onClick={() => void searchKnowledge()}>检索</Button></Space.Compact>{searchDegraded ? <Alert className="search-alert" type="warning" showIcon message="向量不可用，本次结果来自全文降级检索" /> : null}<div className="search-results">{knowledgeHits.length ? knowledgeHits.map((hit) => <Card size="small" className="search-hit" key={hit.chunk_id} title={hit.title} extra={<Tag>{hit.retrieval_mode} · {hit.score.toFixed(3)}</Tag>}><Typography.Paragraph ellipsis={{ rows: 4, expandable: true }}>{hit.content}</Typography.Paragraph><Typography.Text type="secondary">来源：{hit.source_uri} · 分块 {hit.ordinal}</Typography.Text></Card>) : <div className="empty">输入关键词开始检索；结果会显示来源和分块位置。</div>}</div></div> },
      ]} onChange={(key) => { if (key === "recycle" && tenantId) void loadRecycle(tenantId); if (key === "pending" && tenantId) void loadPending(tenantId); }} />
    </Card>
  </>;
}
