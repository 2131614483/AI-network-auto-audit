const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const asar = require('../../desktop/node_modules/@electron/asar');
const { verifyDesktop } = require('../verify-packaged-desktop.cjs');

async function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'audit-package-binding-'));
  const app = path.join(root, 'app'), output = path.join(app, 'out');
  for (const [relative, data] of Object.entries({ 'main/index.js': 'require("electron");require("node:fs");',
    'preload/index.js': 'require("electron");', 'renderer/index.html': '<script src="./assets/current.js"></script>',
    'renderer/assets/current.js': 'console.log("current");' })) {
    const target = path.join(output, relative); fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, data);
  }
  fs.writeFileSync(path.join(app, 'package.json'), JSON.stringify({ main: './out/main/index.js', version: '0.1.0-test' }));
  const archive = path.join(root, 'app.asar'); await asar.createPackage(app, archive);
  return { root, app, output, archive };
}

test('the real ASAR passes only when all compiled bytes and version match', async () => {
  const f = await fixture();
  const result = verifyDesktop(f.output, f.archive, '0.1.0-test');
  assert.equal(result.files.length, 4);
  assert.throws(() => verifyDesktop(f.output, f.archive, 'different'), /version/);
  fs.appendFileSync(path.join(f.output, 'main/index.js'), '\n// startup fix missing from old archive');
  assert.throws(() => verifyDesktop(f.output, f.archive, '0.1.0-test'), /main.*mismatch/);
});

test('a missing renderer chunk cannot silently produce an installer', async () => {
  const f = await fixture();
  fs.writeFileSync(path.join(f.output, 'renderer/assets/missing.js'), 'new chunk');
  assert.throws(() => verifyDesktop(f.output, f.archive, '0.1.0-test'), /missing/);
});

test('the standalone runtime rejects an unbundled main dependency', async () => {
  const f = await fixture();
  fs.appendFileSync(path.join(f.output, 'main/index.js'), '\nrequire("unbundled-third-party");');
  const archive = path.join(f.root, 'external.asar'); await asar.createPackage(f.app, archive);
  assert.throws(() => verifyDesktop(f.output, archive, '0.1.0-test'), /unbundled/);
});

test('developer node_modules cannot be copied into the standalone archive', async () => {
  const f = await fixture();
  const dependency = path.join(f.app, 'node_modules/stale/index.js');
  fs.mkdirSync(path.dirname(dependency), { recursive: true }); fs.writeFileSync(dependency, 'stale');
  const archive = path.join(f.root, 'developer.asar'); await asar.createPackage(f.app, archive);
  assert.throws(() => verifyDesktop(f.output, archive, '0.1.0-test'), /node_modules/);
});
