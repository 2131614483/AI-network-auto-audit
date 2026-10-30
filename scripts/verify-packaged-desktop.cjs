/* Release gate: verify the desktop bytes that will actually be installed. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { isBuiltin } = require('node:module');
const asar = require('../desktop/node_modules/@electron/asar');

function verifyDesktop(output, archive, version) {
  if (asar.listPackage(archive).some(name => /[\\/]node_modules[\\/]/.test(name))) {
    throw new Error('standalone archive contains developer node_modules');
  }
  const metadata = JSON.parse(asar.extractFile(archive, 'package.json'));
  if (metadata.version !== version) throw new Error('packaged version mismatch');
  if (path.normalize(metadata.main) !== path.join('out', 'main', 'index.js')) throw new Error('invalid packaged main entry');
  for (const entry of ['main/index.js', 'preload/index.js', 'renderer/index.html']) {
    if (!fs.statSync(path.join(output, entry)).isFile()) throw new Error(`missing compiled entry: ${entry}`);
  }
  const files = [];
  function walk(directory) {
    for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
      const full = path.join(directory, item.name);
      if (item.isSymbolicLink()) throw new Error('compiled output must not contain symlinks');
      if (item.isDirectory()) { walk(full); continue; }
      const relative = path.relative(output, full), original = fs.readFileSync(full);
      let packed;
      try { packed = asar.extractFile(archive, path.join('out', relative)); }
      catch { throw new Error(`missing packaged file: ${relative}`); }
      if (!original.equals(packed)) throw new Error(`packaged ${relative} mismatch`);
      if (/^(main|preload)[\\/].*\.js$/.test(relative)) {
        for (const match of original.toString('utf8').matchAll(/\brequire\(\s*["']([^"']+)["']\s*\)/g)) {
          if (match[1] !== 'electron' && !isBuiltin(match[1])) throw new Error(`unbundled runtime dependency: ${match[1]}`);
        }
      }
      files.push({ path: `out/${relative.split(path.sep).join('/')}`, sha256: crypto.createHash('sha256').update(packed).digest('hex') });
    }
  }
  walk(output);
  return { ok: true, version, main: metadata.main, files };
}

module.exports = { verifyDesktop };
if (require.main === module) {
  try {
    const result = verifyDesktop(path.resolve(process.argv[2]), path.resolve(process.argv[3]), process.argv[4]);
    if (process.argv[5]) fs.writeFileSync(process.argv[5], JSON.stringify(result, null, 2), { flag: 'wx' });
    console.log(JSON.stringify({ desktop_integrity: result.ok, version: result.version, verified_files: result.files.length }));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
