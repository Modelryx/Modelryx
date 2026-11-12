from __future__ import annotations

import argparse
import logging
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert STEP CAD files to GLB format using OpenCASCADE",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", type=str, help="Input STEP file path (.step, .stp)")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output GLB file path (default: same name as input with .glb extension)",
    )
    parser.add_argument(
        "-q",
        "--quality",
        type=float,
        default=0.5,
        help="Tessellation quality (0.1-1.0, lower = finer mesh, larger file). "
        "Use 0.1-0.3 for curved surfaces, 0.4-0.6 for general use, 0.7-1.0 for preview.",
    )
    parser.add_argument(
        "--no-angular",
        action="store_true",
        help="Disable angular deflection (faster but lower quality on curves)",
    )
    parser.add_argument(
        "--no-edges",
        action="store_true",
        help='Disable edge export (faster but no "Shaded with Edges" support)',
    )
    parser.add_argument(
        "--validate", action="store_true", help="Perform quality validation and report issues"
    )
    return parser


def run_validation(output_path: str, input_path: str, logger: logging.Logger) -> None:
    try:
        from validate_glb import validate_glb

        sidecar_path = Path(input_path).with_suffix(".colors.json")
        validate_glb(output_path, sidecar_path if sidecar_path.exists() else None, verbose=True)
    except ImportError:
        logger.warning("Validation skipped because validate_glb.py was not found")
    except Exception as exc:
        logger.warning("Validation failed: %s", exc)
