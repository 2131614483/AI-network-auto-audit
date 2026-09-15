SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false);
UPDATE topology.routing_plans
SET mission_key = 'audit.ledger.validate|quant.research-note.draft'
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND mission_key LIKE '%:superseded-cw0b';
SELECT plan_key, mission_key FROM topology.routing_plans
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND mission_key = 'audit.ledger.validate|quant.research-note.draft'
ORDER BY id DESC LIMIT 3;
