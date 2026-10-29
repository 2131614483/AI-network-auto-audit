# Knowledge upload approval retry

`knowledge.upload` is the first capability that consumes approval leases. Other
capabilities retain their existing gateway behavior; approval is not a blanket grant.

1. The initial request binds tenant, capability, file paths and bytes to its
   `Idempotency-Key`, persists REQUIRE_APPROVAL and stages no files.
2. An active tenant principal can approve the corresponding approval. A retry
   must use the original key and identical files. A new key requires a new approval.
3. The gateway re-evaluates current rules before honoring a lease. DENY and FREEZE
   always win. Expired leases and inactive approving principals cannot authorize work.
4. Lease consumption and the updated idempotent decision commit together under a
   database lock. The original REQUIRE_APPROVAL and approval history are retained.
5. The consumed lease may resume only its original idempotent request while valid.
   Completed uploads replay the same batch; another key, tenant or body cannot use it.
6. Clients retain the upload key across approval and transient errors, and replace
   it when the selected files or tenant changes. Duplicate submission is disabled.

Verification uses synthetic tenants in an explicitly named local test database.
This contract does not introduce public web authentication or approve plugin execution.

For a newly initialized packaged workspace, explicit initialization also records
one active human principal for the operating-system workspace owner. Its identifier
is stored in `workspace-owner.json`; this is local ownership metadata, not a login
credential. The Electron main process alone reads it and offers a native, default
cancel confirmation for a pending `knowledge.upload` approval in that tenant.
The renderer cannot choose an actor or approve another capability through this IPC.
Source deployments require an explicitly provisioned reviewer and the approval API.
