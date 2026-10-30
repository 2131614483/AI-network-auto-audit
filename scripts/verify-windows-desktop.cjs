/* Actual packaged executable smoke, using only an owned synthetic workspace. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn, spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');

async function main() {
  const unpacked = path.resolve(process.argv[2]);
  const parent = path.resolve(process.argv[3] || 'build');
  const executable = path.join(unpacked, 'AuditNetwork.exe');
  assert.ok(fs.existsSync(executable), 'packaged executable missing');
  fs.mkdirSync(parent, { recursive: true });
  const root = fs.mkdtempSync(path.join(parent, 'native-desktop-test-'));
  const profile = path.join(root, 'synthetic-user'), data = path.join(profile, 'data');
  fs.mkdirSync(data, { recursive: true });
  const port = 56000 + Math.floor(Math.random() * 6000), credentials = { pgPort: port, apiPort: port + 1 };
  for (const key of ['bootstrapPassword', 'appPassword', 'migratorPassword']) credentials[key] = crypto.randomBytes(24).toString('hex');
  fs.writeFileSync(path.join(data, 'runtime.json'), JSON.stringify(credentials));
  const bundle = path.join(root, 'backend.cjs');
  require('esbuild').buildSync({ entryPoints: [path.join(__dirname, '../desktop/electron/backend.ts')], bundle: true,
    platform: 'node', format: 'cjs', outfile: bundle });
  const backend = require(bundle), capture = path.join(root, 'workspace.png');
  const resources = path.join(unpacked, 'resources');
  const result = { test_only: true, native_executable_tested: true, root, ok: false };
  let child;
  try {
    await backend.startPackagedBackend({ resourcesPath: resources, userDataPath: profile,
      requestMaintenance: async action => { assert.equal(action.kind, 'initialize'); return true; }, onLog() {} });
    await backend.stopPackagedBackend();
    const env = { ...process.env, AUDIT_NETWORK_SKIP_DOTENV: '1',
      HTTP_PROXY: 'http://127.0.0.1:9', HTTPS_PROXY: 'http://127.0.0.1:9', NO_PROXY: 'localhost,127.0.0.1,::1',
      AUDIT_NETWORK_CAPTURE_PATH: capture, AUDIT_NETWORK_CAPTURE_QUIT: '1',
      AUDIT_NETWORK_CAPTURE_REQUIRE_ONLINE: '1', AUDIT_NETWORK_CAPTURE_DELAY_MS: '20000', AUDIT_NETWORK_CAPTURE_SETTLE_MS: '1000' };
    delete env.ELECTRON_RUN_AS_NODE;
    delete env.ELECTRON_RENDERER_URL;
    for (const key of ['AUDIT_NETWORK_CAPTURE_VIEW', 'AUDIT_NETWORK_OPEN_BRAIN', 'AUDIT_NETWORK_OPEN_NEBULA', 'AUDIT_NETWORK_OPEN_FLOW']) delete env[key];
    const output = fs.openSync(path.join(root, 'native-process.log'), 'wx');
    child = spawn(executable, [`--user-data-dir=${profile}`, '--proxy-server=http://127.0.0.1:9',
      '--proxy-bypass-list=localhost;127.0.0.1;[::1]'], { env, windowsHide: true, stdio: ['ignore', output, output] });
    fs.closeSync(output);
    result.exit_code = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('native desktop did not finish within 120 seconds')), 120000);
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); resolve(code); });
    });
    assert.equal(result.exit_code, 0, 'native desktop exited with an error');
    assert.ok(fs.existsSync(capture), 'native desktop exited before rendering the workspace');
    assert.equal(fs.readFileSync(capture).subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    const logs = fs.readdirSync(data).filter(name => /^unified-.*\.log$/.test(name))
      .map(name => fs.readFileSync(path.join(data, name), 'utf8')).join('\n');
    assert.ok(logs.includes('desktop_capture_complete connection=online'), 'renderer did not confirm an online workspace');
    result.renderer_online = true;
    result.screenshot = capture;
    result.ok = true;
  } catch (error) {
    result.error = error.message; process.exitCode = 1;
  } finally {
    if (child && child.exitCode === null && child.pid) {
      spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    }
    await backend.stopPackagedBackend();
    const pgCtl = path.join(resources, 'runtime/pgsql/bin/pg_ctl.exe'), pgData = path.join(data, 'pgdata');
    const status = spawnSync(pgCtl, ['-D', pgData, 'status'], { windowsHide: true, stdio: 'ignore' });
    result.postgres_stopped = status.status === 3;
    if (!result.postgres_stopped) {
      spawnSync(pgCtl, ['-D', pgData, 'stop', '-m', 'fast', '-w'], { windowsHide: true, stdio: 'ignore' });
      result.ok = false; process.exitCode = 1;
    }
    fs.writeFileSync(path.join(root, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
