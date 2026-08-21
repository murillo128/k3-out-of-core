#!/usr/bin/env python3
"""Regenerate and validate every main-paper and appendix figure."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from fetch_issue77 import ensure_issue77_inputs
from fetch_issue99 import ensure_issue99_inputs
from fetch_issue98 import ensure_issue98_inputs
from fetch_issue105 import ensure_issue105_inputs
from scripts.common import FIGURES, GENERATED, ROOT, sha256


FIGURE_SCRIPTS = [
    ("fig01-memory-mismatch", "fig01_memory_mismatch.py"),
    ("research-journey", "research_journey.py"),
    ("fig02-workload-dependence", "fig02_workload_dependence.py"),
    ("fig03-policy-selection", "fig03_policy_selection.py"),
    ("fig04-architecture", "fig04_architecture.py"),
    ("fig05-bounded-routing", "fig05_bounded_routing.py"),
    ("fig06-stagec-comparison", "fig06_stagec_comparison.py"),
    ("fig07-loads-to-tps", "fig07_loads_to_tps.py"),
    ("fig08-exact-cache-equivalence", "fig08_exact_cache_equivalence.py"),
    ("fig09-quality-feedback", "fig09_quality_feedback.py"),
    ("fig10-decision-quality", "fig10_decision_quality.py"),
    ("figa1-route-structure", "figa1_route_structure.py"),
    ("figa2-prefill-depth", "figa2_prefill_depth.py"),
    ("figa3-systems-horizon", "figa3_systems_horizon.py"),
    ("figa4-capacity-quality", "figa4_capacity_quality.py"),
    ("figa5-core-periphery", "figa5_core_periphery.py"),
]


def validate_contract() -> None:
    readme = (FIGURES / "README.md").read_text(encoding="utf-8")
    paper = (ROOT / "paper" / "paper.md").read_text(encoding="utf-8")
    for stem, script in FIGURE_SCRIPTS:
        provenance = FIGURES / "provenance" / f"{stem}.md"
        if not provenance.is_file():
            raise FileNotFoundError(f"missing provenance: {provenance}")
        provenance_text = provenance.read_text(encoding="utf-8")
        for suffix in ("svg", "pdf", "png"):
            output = GENERATED / f"{stem}.{suffix}"
            if not output.is_file() or output.stat().st_size == 0:
                raise FileNotFoundError(f"missing generated output: {output}")
            output_sha256 = sha256(output)
            if output_sha256 not in provenance_text:
                raise AssertionError(f"provenance does not record current SHA-256 for {output}")
        for required in (script, f"generated/{stem}.svg", f"provenance/{stem}.md"):
            if required not in readme:
                raise AssertionError(f"README does not reference {required}")
        if f"figures/generated/{stem}.svg" not in paper:
            raise AssertionError(f"paper does not reference figures/generated/{stem}.svg")

    expected_outputs = {
        f"{stem}.{suffix}" for stem, _ in FIGURE_SCRIPTS for suffix in ("svg", "pdf", "png")
    }
    actual_outputs = {
        path.name for path in GENERATED.iterdir() if path.is_file() and path.name != "checksums.json"
    }
    if actual_outputs != expected_outputs:
        raise AssertionError(
            f"generated output inventory mismatch: missing={sorted(expected_outputs - actual_outputs)!r}, "
            f"unexpected={sorted(actual_outputs - expected_outputs)!r}"
        )

    audited = [
        FIGURES / "README.md",
        FIGURES / "generate_all.py",
        FIGURES / "fetch_issue77.py",
        FIGURES / "fetch_issue98.py",
        FIGURES / "fetch_issue99.py",
        FIGURES / "fetch_issue105.py",
    ]
    audited += list((FIGURES / "scripts").glob("*.py"))
    audited += list((FIGURES / "provenance").glob("*.md"))
    for path in audited:
        content = path.read_text(encoding="utf-8")
        if ("/" + "mnt/") in content:
            raise AssertionError(f"host-specific dependency found in {path}")
        if ("issue105-curated-analysis-v" + "1") in content or ("issue105-curated-analysis-v" + "2") in content:
            raise AssertionError(f"superseded #105 release reference found in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="require every release-only input to be cached and verified")
    args = parser.parse_args()

    ensure_issue77_inputs(offline=args.offline)
    ensure_issue98_inputs(offline=args.offline)
    ensure_issue99_inputs(offline=args.offline)
    ensure_issue105_inputs(offline=args.offline)
    env = dict(os.environ)
    env.setdefault("SOURCE_DATE_EPOCH", "0")
    for stem, script in FIGURE_SCRIPTS:
        print(f"generate {stem}", flush=True)
        subprocess.run([sys.executable, str(FIGURES / "scripts" / script)], cwd=ROOT, env=env, check=True)

    validate_contract()
    hashes = {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(GENERATED.iterdir())
        if path.is_file() and path.name != "checksums.json"
    }
    (GENERATED / "checksums.json").write_text(
        json.dumps({"schema_version": "paper-figures-issue124-v3", "outputs": hashes}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"validated {len(FIGURE_SCRIPTS)} figures; wrote {GENERATED / 'checksums.json'}")


if __name__ == "__main__":
    main()
