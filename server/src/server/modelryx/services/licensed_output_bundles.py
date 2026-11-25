"""Compatibility entry point; publication uses CAD-neutral worker bundle contracts."""

from modelryx.services.worker_output_bundles import validate_worker_bundle
from modelryx.services.worker_output_policy import BUNDLE_CONTRACTS


def validate_solidworks_bundle(path, role, max_bytes, max_pdf_bytes):
    return validate_worker_bundle(path, role, max_bytes, max_pdf_bytes,
                                  contract=BUNDLE_CONTRACTS.get(role))
