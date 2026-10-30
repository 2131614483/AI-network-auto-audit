/* Read-path acceptance for a new synthetic packaged workspace; no cloud calls. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const esbuild = require('esbuild');

async function main() {
  // Do not inherit a developer's model setup or dotenv values into this test.
  process.env.AUDIT_NETWORK_SKIP_DOTENV = '1';
  process.env.AUDIT_NETWORK_COLLABORATIVE_PLANNING = '0';
  const resources = path.resolve(process.argv[2]);
  const parent = path.resolve(process.argv[3] || 'build');
  fs.mkdirSync(parent, { recursive: true });
  const root = fs.mkdtempSync(path.join(parent, 'read-acceptance-'));
  const userDataPath = path.join(root, 'synthetic-user'), data = path.join(userDataPath, 'data');
  fs.mkdirSync(data, { recursive: true });
  const port = 56000 + Math.floor(Math.random() * 6000), credentials = { pgPort: port, apiPort: port + 1 };
  for (const field of ['bootstrapPassword', 'appPassword', 'migratorPassword']) credentials[field] = crypto.randomBytes(24).toString('hex');
  fs.writeFileSync(path.join(data, 'runtime.json'), JSON.stringify(credentials));
  const bundle = path.join(root, 'backend.cjs');
  esbuild.buildSync({ entryPoints: [path.join(__dirname, '../desktop/electron/backend.ts')], bundle: true, platform: 'node', format: 'cjs', outfile: bundle });
  const backend = require(bundle), result = { test_only: true, root, checks: [], ok: false };
  const routes = [
    ['workspace', '/api/v1/ui/bootstrap'], ['knowledge', '/api/v1/knowledge/stats'],
    ['graph-proposals', '/api/v1/graph/extractions/proposals?limit=50'],
    ['audit', '/api/v1/audit/engagements?limit=50'], ['quant', '/api/v1/quant/backtests?limit=50'],
    ['aiops', '/api/v1/aiops/incidents?limit=50'], ['evidence', '/api/v1/topology/evidence/status'],
    ['approvals', '/api/v1/approvals'], ['planning-before-setup', '/api/v1/topology/planning/catalog'],
  ];
  try {
    const handle = await backend.startPackagedBackend({ resourcesPath: resources, userDataPath,
      requestMaintenance: async action => action.kind === 'initialize', onLog() {} });
    const tenantResponse = await fetch(`${handle.apiBaseUrl}/api/v1/ui/tenant-context`);
    if (!tenantResponse.ok) throw new Error(`tenant context returned ${tenantResponse.status}`);
    const tenant = (await tenantResponse.json()).tenant_id;
    async function check(name, route) {
      const response = await fetch(handle.apiBaseUrl + route, { headers: { 'X-Tenant-Id': tenant, 'X-Trace-Id': crypto.randomUUID() } });
      const body = await response.json().catch(() => ({}));
      const item = { name, route, status: response.status, decision: body.detail?.decision?.decision || null };
      result.checks.push(item);
      return item;
    }
    for (const [name, route] of routes) await check(name, route);
    const settings = await fetch(handle.apiBaseUrl + '/api/v1/ai/settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': tenant,
        'X-Trace-Id': crypto.randomUUID(), 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ planning_mode: 'external', provider: 'openai_compat', model: 'acceptance-fixture',
        base_url: 'http://127.0.0.1:9/v1', api_key: 'synthetic-not-a-real-key' }),
    });
    if (!settings.ok) throw new Error(`synthetic model settings returned ${settings.status}`);
    process.env.AUDIT_NETWORK_SKIP_DOTENV = '0';
    const setup = await backend.enablePackagedPlanning(async preview => preview.endpoint === 'http://127.0.0.1:9/v1');
    if (!setup.enabled) throw new Error('synthetic planning setup was not enabled');
    await check('planning-after-setup', '/api/v1/topology/planning/catalog');
    result.ok = result.checks.every(item => item.name === 'planning-before-setup' ? item.status === 409 : item.status === 200);
    if (!result.ok) process.exitCode = 1;
  } catch (error) { result.error = error.message; process.exitCode = 1; }
  finally {
    await backend.stopPackagedBackend();
    const { spawnSync } = require('node:child_process');
    const status = spawnSync(path.join(resources, 'runtime/pgsql/bin/pg_ctl.exe'),
      ['-D', path.join(data, 'pgdata'), 'status'], { windowsHide: true, stdio: 'ignore' });
    result.postgres_stopped = status.status === 3;
    if (!result.postgres_stopped) { result.ok = false; process.exitCode = 1; }
    fs.writeFileSync(path.join(root, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
