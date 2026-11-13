# S3 object operations

`modelryx.services.s3_storage.S3ObjectStorage` provides managed uploads, streaming
reads, atomic downloads to local workspaces, existence checks, and deletes against
an existing S3-compatible bucket. Install the optional dependency from `server/`:

```bash
pip install ../worker-sdk
pip install -e '.[s3]'
```

```python
from pathlib import Path
from modelryx.services.s3_storage import S3ObjectStorage

storage = S3ObjectStorage("engineering-files", region_name="us-east-1", prefix="modelryx")
storage.upload_file("parts/example.step", Path("example.step"))
if storage.exists("parts/example.step"):
    storage.download_file("parts/example.step", Path("workspace/example.step"))
with storage.open_download("parts/example.step") as stream:
    first_bytes = stream.read(1024)
storage.delete("parts/example.step")
```

Credentials use Boto3's standard AWS credential chain (for example an IAM role or
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, with `AWS_SESSION_TOKEN` when needed).
Do not embed credentials in source. For a compatible service, supply `endpoint_url`
and, if needed, `addressing_style="path"`. TLS certificate verification remains enabled.
The bucket must already exist. Grant object read/write/delete permissions for the
chosen prefix; HEAD requires read access. Missing objects map to `FileNotFoundError`
for reads and `False` for existence checks. Permission, network, and service errors
propagate. Versioned buckets retain historical versions after delete.

These operations are available to Python callers. The API and processing workflows
still use local storage; this module does not migrate files or enable a deployment
backend switch. Their filesystem-dependent upload, preview, and conversion paths
need integration before S3 can replace the application's primary storage.
