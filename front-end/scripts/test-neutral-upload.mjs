import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Exercise pure upload logic and its API payload without mounting the browser UI.
let proposal;
let submitted = false;
const entries = [
  ['root.assembly', 'ASSEMBLY'],
  ['refs/child.part', 'PART'],
  ['sheets/plan.sheet', 'DRAWING'],
  ['refs/material.dat', 'SUPPORTING'],
].map(([path, documentType], index) => ({
  file: { name: path.split('/').at(-1) }, logicalPath: path, documentType,
  externalCadSystem: 'Example CAD', status: 'ready', itemNumber: `ITEM-${index}`,
  itemName: path, revision: 'A', description: '', category: '',
}));
const staging = {
  id: 'stage', snapshot_version: 1,
  files: entries.map((entry, index) => ({ id: `file-${index}`, logical_path: entry.logicalPath,
    state: 'VERIFIED' })),
};
const workflow = {
  api: async (path, method, body) => {
    if (path.endsWith('/proposal')) { proposal = body; return staging; }
    if (path.endsWith('/submit')) { submitted = true; return { progress_percent: 10 }; }
    assert.equal(method, undefined);
    return staging;
  },
  authorizationHeader: () => ({}), serverApiUrl: (path) => path,
};
function load(path, dependencies) {
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
const model = load('../src/pages/checkinNew/model.ts', { '../../workflow-api': workflow });
assert.equal(model.cadProfile('root.custom', 'ASSEMBLY').artifactType, 'CAD_ASSEMBLY');
assert.equal(model.cadProfile('root.custom', 'ASSEMBLY').cadSystem, 'Unknown');
assert.equal(model.isDrawing(entries[2]), true);
assert.equal(model.createsManagedItem(entries[3], { root_logical_path: 'root.assembly' }), false);
const references = [1, 2].map(() => ({ parent_logical_path: 'root.assembly',
  child_logical_path: 'refs/child.part', instance_name: 'Child:1' }));
references.unshift({ parent_logical_path: 'root.assembly', child_logical_path: 'refs/material.dat',
  properties: { is_bom_occurrence: false } });
references.push({ parent_logical_path: 'sheets/plan.sheet', child_logical_path: 'refs/child.part',
  properties: { is_bom_occurrence: false } });
assert.equal(model.managedBomReferences(references).length, 2);
const names = model.bomInstanceNames(model.managedBomReferences(references));
assert.equal(new Set(names).size, 2);
const actions = load('../src/pages/checkinNew/assemblyUploadActions.ts', {
  '../../workflow-api': workflow, '../../uuid': { randomUUID }, './model': model,
});
await actions.createAssemblyUploadActions({
  files: entries, mainFileName: 'root.assembly', stagingId: 'stage',
  dependencyReport: { root_logical_path: 'root.assembly', references },
  reviewIdentities: async () => true,
  updateEntry: (_index, update) => assert.equal(update.formError, undefined),
  setFiles: (update) => {
    for (const entry of update(entries)) assert.equal(entry.formError, undefined);
  },
  setCommittedItemId: () => {}, setCommitMessage: () => {},
  setStagingSnapshotVersion: () => {}, targetItemNumber: '', targetItemName: '',
}).uploadAssembly();
assert.ok(submitted, 'Native assembly must reach submission');
assert.equal(proposal.revision.cad_system, 'Example CAD');
assert.equal(proposal.identity_decisions.length, 2, 'Only root and part create managed Items');
assert.equal(proposal.bom.length, 2, 'Package dependencies do not create managed BOM lines');
assert.equal(new Set(proposal.bom.map((line) => line.instance_name)).size, 2,
  'Filtered repeated occurrences retain distinct instance names');
assert.deepEqual(proposal.artifacts.map((artifact) => artifact.artifact_type),
  ['CAD_ASSEMBLY', 'DRAWING', 'CAD_MODEL']);
assert.equal(proposal.artifacts[1].files[0].staged_file_id, 'file-2');
assert.ok(proposal.artifacts[0].files.some((file) => file.staged_file_id === 'file-3'),
  'Support bytes remain in the authoritative package');
assert.ok(!proposal.artifacts[0].files.some((file) => file.staged_file_id === 'file-2'),
  'Drawings have their own authoritative Artifact');
console.log('Neutral upload contract checks passed.');
