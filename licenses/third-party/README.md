# Third-party license reference files

See the root [third-party notices](../../THIRD_PARTY_NOTICES.md) for component
attribution and [release packaging guidance](../../docs/operations/third-party-licensing.md).

`sources.json` records the upstream URL and SHA-256 for every downloaded text.
Native/runtime files are reference copies from the release identified in their
URL; they are not a lockfile or an inventory of a built Modelryx image. Frontend
npm copies match the runtime package versions in the lockfile at collection time.
The .NET third-party notice file is for the referenced runtime release, not every
.NET 8 servicing version.

These texts must remain verbatim. Add the exact package notices, copyright files,
source information, and exceptions needed by each shipped artifact. Updating a
dependency can require updating these documents as well.
