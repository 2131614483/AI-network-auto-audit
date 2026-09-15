"""Alert sinks for watchdog findings (R2 remediation: O6 no alert channel).

A sink is a thin, fail-safe delivery adapter.  Dispatch never raises: a broken
sink is logged and reported in the result so the supervisor can keep running.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Protocol

from packages.ops.checks import Finding

logger = logging.getLogger("audit.ops.alerts")


class AlertSink(Protocol):
    def deliver(self, findings: list[Finding]) -> dict[str, Any]:
        """Deliver findings; must not raise."""


class LogSink:
    """Default sink: emit critical/warning findings to the ops logger."""

    def __init__(self, name: str = "audit.ops") -> None:
        self._logger = logging.getLogger(name)

    def deliver(self, findings: list[Finding]) -> dict[str, Any]:
        critical = [f for f in findings if f.severity == "crit"]
        warnings = [f for f in findings if f.severity == "warn"]
        for finding in critical:
            self._logger.error("[%s] %s (metric=%s)", finding.check, finding.message, finding.metric)
        for finding in warnings:
            self._logger.warning("[%s] %s (metric=%s)", finding.check, finding.message, finding.metric)
        return {"ok": True, "critical": len(critical), "warnings": len(warnings)}


class WebhookSink:
    """Generic JSON webhook (e.g. DingTalk/Feishu bot adapter or a local relay).

    Only findings with severity ``warn`` or higher are posted; ``ok`` findings
    are deliberately dropped to avoid noise at 24x7 cadence.
    """

    def __init__(self, url: str, *, timeout: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout

    def deliver(self, findings: list[Finding]) -> dict[str, Any]:
        payload = {
            "source": "audit-network-watchdog",
            "findings": [
                {"check": f.check, "severity": f.severity, "message": f.message, "metric": f.metric}
                for f in findings
                if f.severity in {"warn", "crit"}
            ],
        }
        if not payload["findings"]:
            return {"ok": True, "skipped": "no actionable findings"}
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        request = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - operator-configured webhook
                return {"ok": True, "status": response.status}
        except Exception as error:  # noqa: BLE001 - sinks must never raise
            logger.exception("webhook delivery failed")
            return {"ok": False, "error": str(error)}


def dispatch_findings(findings: list[Finding], sinks: list[AlertSink]) -> dict[str, Any]:
    """Deliver to every sink; one failing sink never blocks the others."""
    results: dict[str, Any] = {}
    for index, sink in enumerate(sinks):
        try:
            results[f"sink_{index}"] = sink.deliver(findings)
        except Exception as error:  # noqa: BLE001 - sink must never raise
            logger.exception("alert sink %d failed", index)
            results[f"sink_{index}"] = {"ok": False, "error": str(error)}
    return results
