"""pyokf — a small Python library for the Open Knowledge Format (OKF).

Developed and maintained by Gwenlake (https://gwenlake.com).

OKF is an open specification published by Google Cloud that represents
knowledge as a directory of markdown files with YAML frontmatter.
Supports OKF v0.2 (trust, provenance, lifecycle signals) while reading
v0.1 bundles unchanged.
Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md

Quick start::

    from pyokf import Bundle

    bundle = Bundle()
    c = bundle.create(
        "metrics/revenue",
        type="Metric",
        title="Revenue",
        description="Chiffre d'affaires reconnu.",
        status="stable",
        body="# Definition\n\n...",
    )
    c.verify(by="human:sylvain@gwenlake.com")
    print(c.trust_tier)  # 'human-reviewed'
    bundle.save("mon_bundle")
"""

from .bundle import (
    Bundle,
    ConceptNotFound,
    ValidationIssue,
    ValidationReport,
    find_bundle_root,
    is_bundle_root,
)
from .concept import (
    RECOMMENDED_KEYS,
    RESERVED_FILENAMES,
    Concept,
    FrontmatterError,
    Link,
    OKFError,
)
from .trust import (
    HUMAN_REVIEWED,
    MACHINE_CONFIRMED,
    STATUSES,
    UNVERIFIED,
    Source,
    Stamp,
)

__version__ = "0.5.0"

__all__ = [
    "Bundle",
    "Concept",
    "ConceptNotFound",
    "FrontmatterError",
    "HUMAN_REVIEWED",
    "Link",
    "MACHINE_CONFIRMED",
    "OKFError",
    "RECOMMENDED_KEYS",
    "RESERVED_FILENAMES",
    "STATUSES",
    "Source",
    "Stamp",
    "UNVERIFIED",
    "ValidationIssue",
    "ValidationReport",
    "__version__",
    "find_bundle_root",
    "is_bundle_root",
]
