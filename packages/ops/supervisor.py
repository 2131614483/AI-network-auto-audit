"""Watchdog: probe API readiness + worker liveness, restart dead services.

R2 remediation (O1/O5): runs as a Windows scheduled task (``--once``, every few
minutes) or as a persistent loop (``--loop``).  It never kills anything and
never writes business rows; healing is delegated to ``scripts/start-brain.ps1``,
which is idempotent (starts only missing processes).  A minimum interval between
restarts prevents crash loops.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from packages.ops.alerts import AlertSink, LogSink, dispatch_findings
from packages.ops.checks import Finding, collect_findings, worker_liveness

logger = logging.getLogger("audit.ops.supervisor")

DEFAULT_API_URL = "http://127.0.0.1:8010"
DEFAULT_DATABASE_URL = "postgresql://audit_app:admin@localhost:5432/audit_network"
EXPECTED_MIGRATION_HEAD = "0051_deprecate_ops_dead_objects"


def check_api(api_url: str, *, timeout: float = 3.0) -> tuple[bool, str]:
    """GET the readiness probe; returns (ok, detail)."""
    url = f"{api_url.rstrip('/')}/api/v1/health/ready"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local control plane
            payload = json.loads(response.read().decode("utf-8"))
        status = payload.get("status")
        if status in {"ok", "degraded"}:
            return True, f"api ready ({status})"
        return False, f"api unhealthy: {status}"
    except Exception as error:  # noqa: BLE001 - probe must not raise
        return False, f"api unreachable: {error}"


def restart_services(project_root: str | Path) -> dict[str, object]:
    """Invoke the idempotent start script (starts missing API/Worker only)."""
    script = Path(project_root) / "scripts" / "start-brain.ps1"
    try:
        result = subprocess.run(  # noqa: S603 - operator-invoked local launcher
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, text=True, timeout=180,
        )
        return {"ok": result.returncode == 0, "exit_code": result.returncode, "output": result.stdout[-2000:]}
    except Exception as error:  # noqa: BLE001 - supervisor must keep running
        logger.exception("restart_services failed")
        return {"ok": False, "error": str(error)}


def run_watchdog_once(
    *,
    database_url: str = DEFAULT_DATABASE_URL,
    api_url: str = DEFAULT_API_URL,
    project_root: str | Path,
    data_dir: str | Path,
    expected_head: str = EXPECTED_MIGRATION_HEAD,
    restart: bool = True,
    min_restart_interval_seconds: float = 120.0,
    sinks: Sequence[AlertSink] | None = None,
    on_event: Callable[[str], None] | None = None,
    last_restart_at: float | None = None,
) -> dict[str, object]:
    """One supervision pass.  Returns a structured report for Task Scheduler."""
    findings = collect_findings(database_url, data_dir=data_dir, expected_head=expected_head)
    api_ok, api_detail = check_api(api_url)
    workers = worker_liveness(database_url)
    stale_all = bool(workers) and all(bool(w["stale"]) for w in workers)
    now = time.monotonic()
    restart_attempted = False
    suppressed = None

    needs_restart = (not api_ok) or stale_all
    if restart and needs_restart:
        if last_restart_at is not None and now - last_restart_at < min_restart_interval_seconds:
            suppressed = "restart suppressed by min interval"
            logger.warning("%s: %s", suppressed, api_detail)
            findings.append(Finding("api", "crit", f"{suppressed}: {api_detail}", None))
        else:
            logger.warning("restarting services: api_ok=%s workers=%s", api_ok, len(workers))
            outcome = restart_services(project_root)
            restart_attempted = bool(outcome["ok"])
            last_restart_at = now
            on_event and on_event(f"restart attempted: {outcome}")
            if not outcome["ok"]:
                findings.append(Finding("restart", "crit", f"restart failed: {outcome}", None))
    elif not api_ok:
        # No restart was requested/possible; the operator still needs to know.
        findings.append(Finding("api", "crit", api_detail, None))
    elif stale_all and not restart:
        findings.append(Finding("worker_liveness", "crit", "all workers stale and restart disabled", len(workers)))

    if sinks:
        dispatch_findings(findings, list(sinks))
    critical = [f.check for f in findings if f.severity == "crit"]
    report: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "api": {"ok": api_ok, "detail": api_detail},
        "workers": {"known": len(workers), "live": sum(0 if bool(w["stale"]) else 1 for w in workers)},
        "findings": [
            {"check": f.check, "severity": f.severity, "message": f.message, "metric": f.metric}
            for f in findings
        ],
        "restart_attempted": restart_attempted,
        "restart_suppressed": suppressed,
        "critical": critical,
        "exit_code": 1 if critical else 0,
    }
    return report


def watchdog_loop(
    *,
    database_url: str = DEFAULT_DATABASE_URL,
    api_url: str = DEFAULT_API_URL,
    project_root: str | Path,
    data_dir: str | Path,
    interval_seconds: float = 60.0,
    expected_head: str = EXPECTED_MIGRATION_HEAD,
) -> None:
    """Persistent supervision loop; for Task Scheduler prefer --once instead."""
    last_restart_at: float | None = None
    while True:
        try:
            report = run_watchdog_once(
                database_url=database_url, api_url=api_url, project_root=project_root,
                data_dir=data_dir, expected_head=expected_head,
                last_restart_at=last_restart_at,
            )
            if report["restart_attempted"]:
                last_restart_at = time.monotonic()
            logger.info("watchdog pass: api=%s critical=%s", report["api"], report["critical"])
        except Exception:  # noqa: BLE001 - supervisor must never die
            logger.exception("watchdog pass failed")
        time.sleep(max(1.0, interval_seconds))


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Network watchdog")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit (Task Scheduler mode)")
    parser.add_argument("--loop", action="store_true", help="run a persistent loop")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    project_root = Path(__file__).resolve().parents[2]
    data_dir = project_root / ".data"

    if args.loop:
        watchdog_loop(
            database_url=args.database_url, api_url=args.api_url,
            project_root=project_root, data_dir=data_dir, interval_seconds=args.interval,
        )
        return 0
    report = run_watchdog_once(
        database_url=args.database_url, api_url=args.api_url,
        project_root=project_root, data_dir=data_dir, restart=not args.no_restart,
        sinks=[LogSink()],
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    exit_code = report["exit_code"]
    assert isinstance(exit_code, int)
    return exit_code


if __name__ == "__main__":
    sys.exit(_main())
