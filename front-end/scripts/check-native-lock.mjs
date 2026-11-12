import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const { packages } = JSON.parse(
  readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'),
);

// npm can silently omit other platforms when a lockfile is updated beside node_modules.
for (const name of ['rollup', 'esbuild']) {
  const parent = packages[`node_modules/${name}`];
  assert(parent?.optionalDependencies, `${name} must declare its native dependencies`);
  for (const [dependency, version] of Object.entries(parent.optionalDependencies)) {
    if (!dependency.startsWith('@rollup/') && !dependency.startsWith('@esbuild/')) continue;
    const entry = packages[`node_modules/${dependency}`];
    assert(entry, `Lockfile is missing ${dependency}; regenerate it in a clean directory`);
    assert.equal(entry.version, version, `Native dependency version mismatch: ${dependency}`);
  }
}

console.log('Native dependencies are locked for every Rollup and esbuild platform.');
