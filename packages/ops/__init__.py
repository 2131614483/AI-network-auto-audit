"""Operational supervision package (R2 remediation: O1/O5/O6/O7).

Health checks, alert sinks, and the watchdog that keeps the API and Worker
alive for 24x7 unattended operation.  Everything here is infrastructure-level:
no tenant context is required, and no business row is ever written.
"""

from __future__ import annotations
