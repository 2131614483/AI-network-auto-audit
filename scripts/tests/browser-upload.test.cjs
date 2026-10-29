const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function browser() {
  const handlers = new Map(), nodes = new Map(), uploads = [];
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      files: [{ name: 'synthetic.md' }], classList: { toggle() {}, remove() {}, add() {} },
      addEventListener(event, callback) { handlers.set(`${selector}:${event}`, callback); },
    });
    return nodes.get(selector);
  };
  let mode = 'approval', unblock;
  const context = vm.createContext({ document: { querySelector: get, querySelectorAll: () => [] },
    Headers, crypto: require('node:crypto').webcrypto, FormData: class { append() {} },
    fetch: async (url, options) => {
      if (!url.includes('knowledge/upload')) return { ok: true, json: async () => ({ items: [] }) };
      uploads.push(options.headers.get('Idempotency-Key'));
      if (mode === 'waiting') await new Promise(resolve => { unblock = resolve; });
      if (mode === 'error') throw new Error('synthetic connection failure');
      return { ok: mode === 'allow', status: mode === 'allow' ? 200 : 409,
        json: async () => mode === 'allow' ? { accepted: 1, deferred: 0 } : {
          detail: { message: 'approval required', decision: { decision: 'REQUIRE_APPROVAL', tool_call_id: 'synthetic' } },
        } };
    } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/app.js'), 'utf8')
    .replace(/loadWorkspace\(\);\s*$/, ''), context);
  vm.runInContext("state.tenantId='synthetic-tenant'", context);
  return { context, uploads, get, mode: value => { mode = value; }, unblock: () => unblock(),
    submit: () => handlers.get('#knowledge-upload:submit')({ preventDefault() {} }),
    change: () => handlers.get('#knowledge-files:change')() };
}

test('approval and network retries keep the upload key; a new selection replaces it', async () => {
  const b = browser();
  await b.submit();
  assert.match(b.get('#knowledge-result').textContent, /审批/);
  b.mode('error'); await b.submit();
  b.mode('allow'); await b.submit(); await b.submit();
  assert.ok(b.uploads[0]); assert.equal(new Set(b.uploads).size, 1);
  b.change(); await b.submit();
  assert.notEqual(b.uploads[0], b.uploads.at(-1));
});

test('a double click never starts concurrent uploads', async () => {
  const b = browser(); b.mode('waiting');
  const pending = b.submit(); await b.submit();
  assert.equal(b.uploads.length, 1);
  b.mode('allow'); b.unblock(); await pending;
});

test('structured policy and validation failures remain readable', () => {
  const b = browser();
  assert.equal(vm.runInContext("describeError({detail:{message:'clear reason'}},409)", b.context), 'clear reason');
  assert.equal(vm.runInContext("describeError({detail:[{msg:'file required'}]},422)", b.context), 'file required');
});
