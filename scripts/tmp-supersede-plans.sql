SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false);
UPDATE topology.routing_plans
SET mission_key = mission_key || ':superseded-cw0b'
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND plan_json->'nodes' @> '[{"blueprint_key": "cw0b-0ec0a63f-producer"}]';
UPDATE topology.routing_plans
SET mission_key = mission_key || ':superseded-cw0c'
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND plan_json->'nodes' @> '[{"blueprint_key": "cw0c-0ec0a63f-a-producer"}]';
SELECT plan_key, mission_key FROM topology.routing_plans
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND (mission_key LIKE '%superseded%')
ORDER BY id LIMIT 5;
