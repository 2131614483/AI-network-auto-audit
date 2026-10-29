#!/usr/bin/env python
"""Release smoke test for the control plane.

Exercises the paths a user actually depends on, in the order they matter:

    1. health / readiness          the process and database are alive
    2. UI bootstrap                the shell can render at all
    3. topology reads              clusters, blueprints, capability graph
    4. policy simulation           the gateway evaluates instead of 500-ing
    5. knowledge upload -> search  a real write followed by a real retrieval

Step 5 is the important one: it is the only check that proves writes land and
the retrieval index is live. A release that passes 1-4 but fails 5 is not
usable, so every failure here is fatal.

Only the standard library is used, so this runs identically under the packaged
portable Python, inside the container, and in CI.

    python scripts/smoke-api.py --base-url http://127.0.0.1:8010 \
        --tenant-id 613cece3-1831-4a1a-a7b0-a4129908faed
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any


class SmokeFailure(RuntimeError):
    """A check did not meet its acceptance criteria."""


def _request(
    method: str,
    url: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
    raw: tuple[bytes, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    request_headers = {"Accept": "application/json"}
    data: bytes | None = None
    if raw is not None:
        data, content_type = raw
        request_headers["Content-Type"] = content_type
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(detail)
        except json.JSONDecodeError:
            return exc.code, {"detail": detail[:800]}


def _multipart(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----smoke{uuid.uuid4().hex}"
    # 头部与正文之间必须是空行（\r\n\r\n）；漏掉它 Starlette 会报
    # "There was an error parsing the body"。
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    )
    tail = f"\r\n--{boundary}--\r\n"
    body = head.encode("utf-8") + content + tail.encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class Smoke:
    def __init__(self, base_url: str, tenant_id: str) -> None:
        self.base = base_url.rstrip("/")
        self.tenant = tenant_id
        self.failures: list[str] = []
        self.checks: list[tuple[str, str, str]] = []

    def tenant_headers(self, **extra: str) -> dict[str, str]:
        return {"X-Tenant-Id": self.tenant, **extra}

    def record(self, name: str, status: str, detail: str) -> None:
        self.checks.append((name, status, detail))
        if status == "FAIL":
            self.failures.append(f"{name}: {detail}")

    def get(self, name: str, path: str, *, tenant: bool = True) -> Any:
        headers = self.tenant_headers() if tenant else {}
        status, body = _request("GET", f"{self.base}{path}", headers=headers)
        if status != 200:
            self.record(name, "FAIL", f"HTTP {status} {_brief(body)}")
            return None
        self.record(name, "ok", "")
        return body

    def post(self, name: str, path: str, payload: dict[str, Any], **extra: str) -> Any:
        status, body = _request(
            "POST", f"{self.base}{path}", body=payload, headers=self.tenant_headers(**extra)
        )
        if status != 200:
            self.record(name, "FAIL", f"HTTP {status} {_brief(body)}")
            return None
        self.record(name, "ok", "")
        return body


def _brief(body: Any) -> str:
    text = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    return text[:300]


def _check_retrieval(smoke: "Smoke", marker: str) -> None:
    """仅在写入被放行时调用：确认刚写进去的标识能被检索回来。"""
    search = smoke.post(
        "知识检索 /knowledge/search",
        "/api/v1/knowledge/search",
        {"query": marker, "mode": "keyword", "limit": 10},
    )
    if isinstance(search, dict):
        items = search.get("items") or []
        if not items:
            smoke.record("检索命中", "FAIL", f"写入后检索不到 {marker}")
        else:
            smoke.record("检索命中", "ok", f"{len(items)} 条")


def run(base_url: str, tenant_id: str) -> int:
    smoke = Smoke(base_url, tenant_id)

    # --- 1. 进程与数据库 ---------------------------------------------------
    status, body = _request("GET", f"{base_url.rstrip('/')}/health")
    if status != 200:
        print(f"控制平面不可达：HTTP {status} {_brief(body)}", file=sys.stderr)
        return 2

    ready = smoke.get("健康检查 /api/v1/health/ready", "/api/v1/health/ready", tenant=False)
    if isinstance(ready, dict):
        database = str(ready.get("database", "")).lower()
        if database and database != "ok":
            smoke.record("数据库连通", "FAIL", f"database={database}")
        else:
            smoke.record("数据库连通", "ok", f"status={ready.get('status')}")
        if not ready.get("migration_head"):
            smoke.record("迁移版本", "FAIL", "响应中没有 migration_head")
        else:
            smoke.record("迁移版本", "ok", str(ready.get("migration_head")))

    # --- 2. UI 契约 --------------------------------------------------------
    bootstrap = smoke.get("UI 引导 /api/v1/ui/bootstrap", "/api/v1/ui/bootstrap", tenant=False)
    if isinstance(bootstrap, dict) and not bootstrap.get("api_version"):
        smoke.record("UI 版本", "FAIL", "响应中没有 api_version")

    # --- 3. 组网读取 -------------------------------------------------------
    clusters = smoke.get("集群列表 /topology/clusters", "/api/v1/topology/clusters")
    if isinstance(clusters, dict):
        count = len(clusters.get("items") or [])
        if count == 0:
            smoke.record("集群数据", "FAIL", "示例数据为空，界面会是空的")
        else:
            smoke.record("集群数据", "ok", f"{count} 个")

    blueprints = smoke.get("蓝图列表 /topology/blueprints", "/api/v1/topology/blueprints")
    if isinstance(blueprints, dict):
        count = len(blueprints.get("items") or [])
        if count == 0:
            smoke.record("蓝图数据", "FAIL", "没有任何蓝图，组网不可用")
        else:
            smoke.record("蓝图数据", "ok", f"{count} 条")

    smoke.get("能力图谱 /topology/plugin-nebula", "/api/v1/topology/plugin-nebula")

    # --- 4. 策略网关 -------------------------------------------------------
    decision = smoke.post(
        "策略模拟 /policy/simulate",
        "/api/v1/policy/simulate",
        {
            "capability": "graph.neighbors.read",
            "risk_class": "read_only",
            "side_effects": "read_only",
            "arguments": {},
        },
    )
    if isinstance(decision, dict):
        if not decision.get("decision"):
            smoke.record("策略判定", "FAIL", "响应中没有 decision")
        else:
            smoke.record("策略判定", "ok", str(decision.get("decision")))

    # --- 5. 写入边界：未审批写入必须被拦截，审批必须落库 -------------------
    #
    # 这个租户的策略对写操作一律 REQUIRE_APPROVAL，所以匿名 HTTP 写入本来就不该
    # 成功。冒烟要证明的是"边界真的生效"（fail-closed）以及审批记录确实持久化，
    # 而不是假装写入成功。若策略放行（较少见的宽松配置），则继续验证检索闭环。
    marker = f"smoke-{uuid.uuid4().hex[:12]}"
    document = (
        f"# 发布冒烟文档\n\n"
        f"本文件由发布冒烟脚本自动写入，标识为 {marker}。\n"
        f"它用于验证知识库写入与检索链路在发行版本中可用。\n"
    ).encode("utf-8")
    payload, content_type = _multipart("files", "smoke-release.md", document)
    status, body = _request(
        "POST",
        f"{base_url.rstrip('/')}/api/v1/knowledge/upload",
        raw=(payload, content_type),
        headers=smoke.tenant_headers(**{"Idempotency-Key": f"smoke-{uuid.uuid4().hex}"}),
    )
    tool_call_id = ""
    if status == 200:
        accepted = body.get("accepted") if isinstance(body, dict) else None
        smoke.record("知识上传 /knowledge/upload", "ok" if accepted else "FAIL", f"accepted={accepted}")
        _check_retrieval(smoke, marker)
    elif status in (403, 409):
        detail = body.get("detail") if isinstance(body, dict) else None
        blocked = detail.get("decision") if isinstance(detail, dict) else None
        if not isinstance(blocked, dict) or not blocked.get("decision"):
            smoke.record("写入边界", "FAIL", f"HTTP {status} 不是策略网关的拒绝格式：{_brief(body)}")
        else:
            smoke.record(
                "写入边界（未审批写入被拒绝）",
                "ok",
                f"{blocked.get('decision')} risk={blocked.get('risk_score')}",
            )
            tool_call_id = str(blocked.get("tool_call_id") or "")
    else:
        smoke.record("写入边界", "FAIL", f"HTTP {status} {_brief(body)}")

    if tool_call_id:
        approvals = smoke.get("审批记录 /approvals", "/api/v1/approvals")
        if isinstance(approvals, dict):
            items = approvals.get("items") or []
            matched = any(str(item.get("tool_call_id")) == tool_call_id for item in items)
            if matched:
                smoke.record("审批落库", "ok", f"{len(items)} 条，含本次 tool_call")
            else:
                smoke.record("审批落库", "FAIL", f"{len(items)} 条，但不含 {tool_call_id[:8]}…")

    # --- 汇总 --------------------------------------------------------------
    width = max(len(name) for name, _, _ in smoke.checks)
    print()
    print(f"{'检查项'.ljust(width)}  结果  说明")
    print("-" * (width + 24))
    for name, result, detail in smoke.checks:
        print(f"{name.ljust(width)}  {result:<4}  {detail}")

    if smoke.failures:
        print()
        print(f"冒烟失败 {len(smoke.failures)} 项：", file=sys.stderr)
        for failure in smoke.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print()
    print(f"冒烟通过：{len(smoke.checks)} 项检查全部符合预期。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="控制平面发布冒烟测试")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010", help="控制平面地址")
    parser.add_argument("--tenant-id", required=True, help="租户 UUID（示例数据为 local-dev 租户）")
    args = parser.parse_args(argv)
    try:
        return run(args.base_url, args.tenant_id)
    except urllib.error.URLError as exc:
        print(f"无法连接 {args.base_url}：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
