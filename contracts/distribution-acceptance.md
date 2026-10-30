# Distribution acceptance checks

Run only against an owned synthetic packaged workspace and loopback services.
Every route check uses the workspace tenant and a new trace. Each result records
the route, HTTP status and policy decision, not credentials or response data.
Missing permissions, absent services and untested paths are not counted as passes.

Check the initial knowledge, graph, audit, quant, AIOps, evidence, approval and
planning views. A separately confirmed synthetic external-model configuration may
enable the bounded plan-only catalog; a reachable catalog does not mean a real
model call or planning session passed. Any blocked required initial view makes
the overall read-path acceptance fail. Never broaden permissions just to make
this test green.

The graph extraction page must distinguish a pending read, a failed or denied
read, and a successful empty result. A failed refresh must not continue to
present old proposals as current. Changing tenant must not show another
tenant's data or allow a late response from the previous tenant to replace it.
