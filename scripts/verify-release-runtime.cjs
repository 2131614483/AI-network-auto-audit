/* Exercise the real packaged backend in a new, retained synthetic workspace.
 * No Electron window, cloud provider, production database or user workspace.
 * Maintainer prerequisite: npm ci at the repository root (for its build tools).
 * Usage: node scripts/verify-release-runtime.cjs <resources-directory> <report-parent>
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const esbuild = require('esbuild');

async function main() {
  const resources = path.resolve(process.argv[2]);
  const parent = path.resolve(process.argv[3] || 'build');
  fs.mkdirSync(parent, { recursive: true });
  const root = fs.mkdtempSync(path.join(parent, 'release-runtime-test-'));
  const bundle = path.join(root, 'backend.cjs');
  esbuild.buildSync({ entryPoints: [path.join(__dirname, '../desktop/electron/backend.ts')], bundle: true,
    platform: 'node', format: 'cjs', outfile: bundle });
  const { enablePackagedPlanning, startPackagedBackend, stopPackagedBackend } = require(bundle);
  const userDataPath = path.join(root, 'synthetic-user');
  const result = { test_only: true, native_gui_tested: false, root, checks: [], ok: false };
  const options = { resourcesPath: resources, userDataPath,
    onLog: message => { if (!message.startsWith('[API]') && !message.startsWith('[worker]')) console.log(message); },
    requestMaintenance: async action => { assert.equal(action.kind, 'initialize'); return true; } };
  const read = async (base, route) => {
    const response = await fetch(base + route);
    assert.equal(response.status, 200, route);
    return response.json();
  };
  try {
    const handle = await startPackagedBackend(options);
    await read(handle.apiBaseUrl, '/health');
    const tenant = await read(handle.apiBaseUrl, '/api/v1/ui/tenant-context');
    assert.ok(tenant.tenant_id);
    result.checks.push('fresh initialization, API health and tenant context');
    const dataDir = path.join(userDataPath, 'data');
    const journalFile = path.join(dataDir, 'maintenance/history.jsonl');
    const journal = fs.readFileSync(journalFile, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(journal.map(row => row.status), ['started', 'succeeded']);
    const credentials = JSON.parse(fs.readFileSync(path.join(dataDir, 'runtime.json'), 'utf8'));
    const python = path.join(resources, 'runtime/python/python.exe');
    const code = [
      'import json, os, psycopg2',
      'c=psycopg2.connect(os.environ["DATABASE_URL"])',
      'q=c.cursor()',
      'q.execute("SELECT rolbypassrls,rolsuper FROM pg_roles WHERE rolname=current_user")',
      'role=q.fetchone()',
      'q.execute("SELECT id FROM iam.tenants WHERE slug=\'local-dev\'")',
      'tenant=str(q.fetchone()[0])',
      'q.execute("SELECT set_config(\'app.tenant_id\',%s,true)",(tenant,))',
      'q.execute("SELECT count(*) FROM policy.tool_calls WHERE capability=\'workspace.maintenance.initialize\'")',
      'count=q.fetchone()[0]',
      'q.execute("SELECT count(*) FROM policy.policy_sets WHERE name LIKE \'managed-maintenance-%\' AND status=\'active\'")',
      'grants=q.fetchone()[0]',
      'print(json.dumps({"bypassrls":role[0],"superuser":role[1],"maintenance_events":count,"maintenance_grants":grants}))',
      'c.close()',
    ].join('\n');
    const check = spawnSync(python, ['-c', code], { cwd: path.join(resources, 'app'), encoding: 'utf8', windowsHide: true,
      env: { ...process.env, AUDIT_NETWORK_SKIP_DOTENV: '1',
        DATABASE_URL: `postgresql://audit_app:${encodeURIComponent(credentials.appPassword)}@127.0.0.1:${credentials.pgPort}/audit_network` } });
    assert.equal(check.status, 0, 'application-role audit query failed; inspect retained workspace logs');
    const facts = JSON.parse(check.stdout);
    assert.equal(facts.bypassrls, false); assert.equal(facts.superuser, false);
    assert.equal(facts.maintenance_events, 2); assert.equal(facts.maintenance_grants, 1);
    result.database_facts = facts;
    result.checks.push('bootstrap journal imported and app role has no BYPASSRLS/superuser');
    const settings = await fetch(handle.apiBaseUrl + '/api/v1/ai/settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': tenant.tenant_id,
        'X-Trace-Id': require('node:crypto').randomUUID(), 'Idempotency-Key': require('node:crypto').randomUUID() },
      body: JSON.stringify({ planning_mode: 'external', provider: 'openai_compat', model: 'deployment-fixture',
        base_url: 'http://127.0.0.1:9/v1', api_key: 'synthetic-not-a-real-key' }),
    });
    assert.equal(settings.status, 200, 'saving synthetic AI settings');
    process.env.AUDIT_NETWORK_SKIP_DOTENV = '0';
    const cancelled = await enablePackagedPlanning(async () => false);
    assert.equal(cancelled.enabled, false);
    assert.equal(fs.readFileSync(journalFile, 'utf8').trim().split('\n').length, 2);
    const enabled = await enablePackagedPlanning(async preview => {
      assert.equal(preview.endpoint, 'http://127.0.0.1:9/v1');
      assert.equal(preview.model, 'deployment-fixture');
      return true;
    });
    assert.equal(enabled.enabled, true);
    const catalogResponse = await fetch(handle.apiBaseUrl + '/api/v1/topology/planning/catalog', {
      headers: { 'X-Tenant-Id': tenant.tenant_id, 'X-Trace-Id': require('node:crypto').randomUUID() },
    });
    assert.equal(catalogResponse.status, 200);
    assert.ok((await catalogResponse.json()).items.length > 0);
    result.checks.push('saved synthetic provider, cancelled setup, explicit bounded planning grant and catalog read');
    await stopPackagedBackend();
    const second = await startPackagedBackend({ ...options, requestMaintenance: async () => {
      throw new Error('ordinary restart must not ask to migrate or initialize');
    } });
    await read(second.apiBaseUrl, '/health');
    assert.equal(fs.readFileSync(journalFile, 'utf8').trim().split('\n').length, 4);
    result.checks.push('ordinary restart performs no maintenance');
    result.ok = true;
  } catch (error) {
    result.error = error instanceof Error ? error.message : String(error);
    process.exitCode = 1;
  } finally {
    await stopPackagedBackend();
    const pg = spawnSync(path.join(resources, 'runtime/pgsql/bin/pg_ctl.exe'),
      ['-D', path.join(userDataPath, 'data/pgdata'), 'status'], { encoding: 'utf8', windowsHide: true });
    result.postgres_stopped = pg.status === 3;
    if (!result.postgres_stopped) { result.ok = false; process.exitCode = 1; }
    fs.writeFileSync(path.join(root, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
