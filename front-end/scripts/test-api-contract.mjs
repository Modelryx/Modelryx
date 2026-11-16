import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const stored = new Map([
  ['modelryx-server', 'https://api.example'], ['modelryx-token', 'test-token'],
]);
globalThis.localStorage = {
  getItem: (key) => stored.get(key) ?? null,
  removeItem: (key) => stored.delete(key),
};
const events = [];
globalThis.window = {
  location: { origin: 'https://frontend.example' },
  dispatchEvent: (event) => events.push(event.type),
};
let calls = [];
let response = { status: 204, ok: true };
globalThis.fetch = async (...args) => { calls.push(args); return response; };
function load(path, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  new Function('require', 'exports', output)((name) => {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  }, exports);
  return exports;
}
const workflow = load('../src/workflow-api.ts');
const core = load('../src/api.ts', { './workflow-api': workflow });
for (const api of [workflow.api, core.api]) {
  for (const path of ['https://other.example/api/v1/items',
    'https://api.example/private', '/api/v1/../private',
    'https://user:password@api.example/api/v1/items']) {
    calls = [];
    await assert.rejects(api(path));
    assert.equal(calls.length, 0, 'Invalid addresses must be rejected before sending credentials');
  }
  calls = [];
  await api('/items', 'PUT', false);
  assert.equal(calls[0][0], 'https://api.example/api/v1/items');
  assert.equal(calls[0][1].body, 'false');
  assert.equal(calls[0][1].headers['Content-Type'], 'application/json');
  assert.equal(calls[0][1].headers.Authorization, 'Bearer test-token');
  response = { status: 401, ok: false, json: async () => ({ detail: 'Expired session' }) };
  await assert.rejects(api('/items'), /Expired session/);
  assert.equal(stored.has('modelryx-token'), false);
  assert.equal(events.at(-1), 'modelryx-auth-required');
  stored.set('modelryx-token', 'test-token');
  response = { status: 204, ok: true };
}
assert.equal(workflow.serverApiUrl('https://api.example/api/v1/items'),
  'https://api.example/api/v1/items');
stored.set('modelryx-server', 'https://api.example/prefix');
assert.equal(workflow.serverApiUrl('/items'), 'https://api.example/prefix/api/v1/items');
stored.delete('modelryx-server');
assert.equal(workflow.serverApiUrl('/items'), '/api/v1/items');
console.log('API client contract checks passed.');
