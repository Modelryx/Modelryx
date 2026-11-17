# Development tooling security

The frontend production bundle and its Node-based build tools have different
dependency scopes. Review both when preparing a release. A clean runtime-only
`npm audit` does not clear findings in development dependencies.

## Current picomatch advisory

As checked on 2026-11-17, the locked build tool chain includes picomatch 4.0.7,
which is affected by [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf)
(CVE-2026-112366). The upstream advisory lists no patched version. Crafted repeated
extglob patterns can cause excessive regular-expression processing.

The local npm audit reports nine high-severity dependency entries because Vite,
fdir, tinyglobby and TypeScript ESLint packages depend on the affected package.
These are related paths to one underlying advisory, not nine independent flaws.
The checked package is in the development tool graph; it is not a runtime
frontend dependency. Modelryx application source does not import picomatch or
accept user-provided glob patterns for these tools. This scope assessment does
not establish that every tool-level input is unreachable by an attacker.

Until an upstream fix or separately reviewed replacement is available:

- Keep build/lint configuration and glob patterns maintainer-controlled. Review
  changes before running them; do not feed user uploads or external pattern lists
  into development tools.
- Use isolated CI runners for untrusted contributions, with bounded execution
  times, minimum permissions and no production credentials.
- `npm run dev` binds to `localhost` (loopback) by default. For deliberately trusted LAN
  development, use `npm run dev -- --host 0.0.0.0` and configure the exact frontend
  CORS/agent origins and host firewall. Loopback binding reduces network exposure;
  it does not repair picomatch's pattern-processing vulnerability.
- Deploy the built `dist` assets through a production web server. Do not expose
  the Vite development server as production hosting.
- Track upstream remediation and rerun both `npm audit` and
  `npm audit --omit=dev` after updating the lockfile. Do not downgrade Vite or
  force an incompatible override solely to silence a transitive audit result.

The release owner accepted this documented known limitation on 2026-11-17 for
the first source release, with the controls above. The advisory remains unresolved
and must be tracked for an upstream fix. This decision does not approve exposing
the development server as production hosting or waive separate binary-release
reviews.
