SELECT set_config('app.tenant_id', (SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false);
BEGIN;
DELETE FROM topology.routing_plan_edges e
USING topology.routing_plans p
WHERE e.plan_id = p.id
  AND p.tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND p.plan_json->'nodes' @> '[{"blueprint_key": "cw0b-0ec0a63f-producer"}]';
DELETE FROM topology.routing_plan_nodes n
USING topology.routing_plans p
WHERE n.plan_id = p.id
  AND p.tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND p.plan_json->'nodes' @> '[{"blueprint_key": "cw0b-0ec0a63f-producer"}]';
DELETE FROM topology.routing_plans
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND plan_json->'nodes' @> '[{"blueprint_key": "cw0b-0ec0a63f-producer"}]';
DELETE FROM topology.plugin_blueprints
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND key LIKE 'cw0b-%';
COMMIT;
SELECT count(*) AS remaining_cw0b_blueprints
FROM topology.plugin_blueprints
WHERE tenant_id = (SELECT id FROM iam.tenants WHERE slug='local-dev')
  AND key LIKE 'cw0b-%';
