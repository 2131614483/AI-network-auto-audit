"""End-to-end canvas AI networking demo via the real control-plane API (SSE).
Simulates exactly what the desktop chat panel does: message -> live stage
events -> compiled graph flow."""
import json
import sys
import time
import urllib.request

import psycopg2

url = "postgresql://audit_app:admin@localhost:5432/audit_network"
with psycopg2.connect(url) as c, c.cursor() as cur:
    cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
    row = cur.fetchone()
    assert row is not None
    tenant_id = str(row[0])

payload = {
    "message": "对日记账质量进行校验，并对校验发现的异常候选生成审计发现草稿",
    "session_id": "chat-uie2e-00000002",
    "idempotency_key": "chat-uie2e-00000010",
    "data_sources": [["ledger-a", "ledger"]],
}
body = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8010/api/v1/topology/canvas/chat/stream",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Tenant-Id": tenant_id,
        "X-Trace-Id": "chat-uie2e-trace-0001",
    },
    method="POST",
)

print("=== 发送任务：对日记账质量进行校验，并对校验发现的异常候选生成审计发现草稿 ===", flush=True)
try:
    with urllib.request.urlopen(req, timeout=900) as resp:
        buffer = ""
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                event_type = "message"
                data_lines = []
                for line in block.splitlines():
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                if not data_lines:
                    continue
                data = json.loads("\n".join(data_lines))
                ts = time.strftime("%H:%M:%S")
                if event_type == "stage":
                    stage = data.get("stage")
                    if stage == "capability_recall":
                        print(f"[{ts}] 召回能力目录（命中 {data.get('capabilities', 0)} 项）", flush=True)
                    elif stage == "llm_draft":
                        print(f"[{ts}] 模型生成草稿（第 {data.get('round')}/{data.get('max_rounds')} 轮，思考中…）", flush=True)
                    elif stage in ("validate", "compile"):
                        label = "校验白名单/数据边界" if stage == "validate" else "确定性编译"
                        if data.get("ok"):
                            print(f"[{ts}] {label}（第 {data.get('round')} 轮）通过", flush=True)
                        else:
                            issues = data.get("issues") or []
                            codes = ",".join(i.get("code", "?") for i in issues)
                            print(f"[{ts}] {label} 未通过：{codes}", flush=True)
                            for issue in issues:
                                print(
                                    f"    > {issue.get('code')}: {issue.get('message')} "
                                    f"(node={issue.get('node_id')} port={issue.get('port_id')}) "
                                    f"-> {issue.get('suggested_action')}",
                                    flush=True,
                                )
                elif event_type == "done":
                    print(f"[{ts}] 完成：status={data.get('status')} plan_key={data.get('plan_key')} revisions={data.get('revisions')}", flush=True)
                    draft = data.get("draft")
                    if draft:
                        print(f"[{ts}] 图谱流程：{len(draft.get('nodes', []))} 节点 / {len(draft.get('edges', []))} 边 / intent_id={data.get('intent_id')}", flush=True)
                        for node in draft.get("nodes", []):
                            print(f"    - 节点 {node.get('node_instance_id')} | {node.get('capability')} | {node.get('plugin_id')}", flush=True)
                        for edge in draft.get("edges", []):
                            print(f"    - 边 {edge.get('source_instance')}:{edge.get('source_port')} -> {edge.get('target_instance')}:{edge.get('target_port')}", flush=True)
                        if draft.get("selection_reasons"):
                            print(f"    选型说明：{draft.get('selection_reasons')}", flush=True)
                        flow_path = r"D:\pythonpro\audit_network\.data\ai-generated-flow.json"
                        with open(flow_path, "w", encoding="utf-8") as fh:
                            json.dump(draft, fh, ensure_ascii=False, indent=2)
                        print(f"[{ts}] 流程文件已写入：{flow_path}", flush=True)
                    else:
                        print(f"    issues: {json.dumps(data.get('issues', []), ensure_ascii=False)[:400]}", flush=True)
                    print(f"[{ts}] 回复：{data.get('reply_text')}", flush=True)
                    break
                elif event_type == "error":
                    print(f"[{ts}] 错误：{data.get('detail')}", flush=True)
                    sys.exit(1)
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code, exc.read().decode("utf-8")[:500], flush=True)
    sys.exit(1)
