SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false);
UPDATE topology.plugin_blueprints
SET status = 'archived'
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND (key LIKE 'cw0b-%' OR key LIKE 'cw0c-%')
  AND status IN ('planned','released');
SELECT key, status FROM topology.plugin_blueprints
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND (key LIKE 'cw0b-%' OR key LIKE 'cw0c-%')
ORDER BY key LIMIT 5;

