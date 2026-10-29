// Packaging uses local runtimes and tools. A missing cache is an explicit error.
for (const name of ['node:http', 'node:https']) {
  const client = require(name);
  client.request = client.get = () => { throw new Error('Network access disabled during release packaging'); };
}
globalThis.fetch = async () => { throw new Error('Network access disabled during release packaging'); };
