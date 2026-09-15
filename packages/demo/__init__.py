"""Local demo data provisioning (R0 remediation).

Idempotent seed + periodic generator so every control-plane feature has real,
reproducible, internally consistent data, and a 24x7 run keeps producing new
AIOps cycles through the same code paths the API uses.
"""

from __future__ import annotations

from packages.demo.generator import generate_aiops_cycle, run_demo_generation_once
from packages.demo.seed import SeedReport, seed_local_demo

__all__ = ["SeedReport", "generate_aiops_cycle", "run_demo_generation_once", "seed_local_demo"]
