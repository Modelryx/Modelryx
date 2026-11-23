# Worker Integrations

This section documents CAD worker APIs and integration points for the Modelryx platform.

## Documentation

### For Integrators

- **[new-cad-worker-guide.md](./new-cad-worker-guide.md)** — Design guide for custom CAD workers, HTTP contract, and deployment patterns
- **[freecad-worker.md](./freecad-worker.md)** — Native FreeCAD worker deployment and configuration
- **[step-worker.md](./step-worker.md)** — STEP and geometry processing service reference implementation

The Modelryx platform includes the FreeCAD and STEP workers as reference implementations. The FreeCAD worker handles native CAD extraction with rich assembly semantics; the STEP worker provides neutral geometry processing. Use the worker guide to design compatible custom workers for additional CAD formats or proprietary systems.

## Open-Source Platform

This open-source repository includes:

- **STEP Worker** — Cross-platform STEP file processing and GLB conversion
- **FreeCAD Worker** — Open-source CAD conversion and processing
- **Modelryx Server** — FastAPI application and processing orchestration

For the native worker protocol and reference implementation, see the [worker SDK](../../worker-sdk/README.md) and [FreeCAD worker](../../workers/freecad/README.md).

## Deployment boundaries

The root Compose stack includes STEP and the general orchestrator. Deploy and register
FreeCAD separately. Native file selection also requires the [Local CAD Agent](../../local_cad_agent/README.md)
and a matching preprocessor on each user's computer; a running remote worker does not install them.

## Architecture

Workers are independently deployable services that:

1. Accept authenticated job submissions from Modelryx
2. Process CAD files and metadata asynchronously
3. Validate and expose hashed, job-scoped outputs for Modelryx to download
4. Communicate job status and results back to the server

Protected worker requests use the shared internal key. Native CAD jobs use protocol job IDs for replay detection; the STEP service uses its separate `Idempotency-Key` contract. Health probes are available without worker credentials.

## See Also

- [Platform Architecture](../architecture/platform-architecture.md) — System design and component boundaries
- [CAD Processing Framework](../architecture/cad-processing-framework.md) — Pipeline details and artifact handling
