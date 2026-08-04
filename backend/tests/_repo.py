"""Locating the repository tree from a test, and skipping when it is absent.

WHY THIS EXISTS
    A cluster of suites here validate *infrastructure artifacts* rather than
    application behaviour: Helm charts, Dockerfiles, the Trivy and SOPS
    policies, SBOM and renovate config, the SLO rules, the compliance docs.
    Those files live at the REPOSITORY root — config/, docker/, scripts/,
    observability/, renovate.json — not inside the importable `app` package.

    The api container image copies only backend/. Run the suite in there and
    every one of those tests raises FileNotFoundError: on a full run that was
    264 failures and 704 errors, all of them the same missing-tree condition.

    That is the wrong signal twice over. Nothing is broken — the checks simply
    cannot run in an environment that does not contain what they inspect — and
    a thousand lines of red is loud enough to bury a genuine regression. It
    did exactly that earlier: a barrier-control bug sat behind that noise.

    So: skip when the tree is absent (truthful, quiet), and run normally
    wherever it IS present — a developer checkout, or CI with the repo cloned.
    The checks keep their value; they just stop lying about the container.
"""
from pathlib import Path

import pytest


def find_repo_root() -> Path | None:
    """Walk up from this file looking for the repository root.

    Identified by `docker/` and `backend/` sitting side by side. Requiring two
    sibling directories rather than one avoids matching a stray `config/` or
    `docker/` folder nested somewhere inside the package itself.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker").is_dir() and (parent / "backend").is_dir():
            return parent
    return None


_found = find_repo_root()

HAS_REPO_TREE: bool = _found is not None

# Always a Path, so module-level constants like `ROOT / "config" / "x.yml"`
# still build at import time when the tree is missing. They simply point at
# something that does not exist, and the marker below skips before any test
# tries to read them.
REPO_ROOT: Path = _found if _found is not None else Path("/__no_repo_tree__")

requires_repo_tree = pytest.mark.skipif(
    not HAS_REPO_TREE,
    reason=(
        "repository tree not available — the api image ships only backend/. "
        "This suite validates repo-root infrastructure files; run it from a "
        "working tree or a CI checkout."
    ),
)
