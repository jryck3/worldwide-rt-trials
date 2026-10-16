#!/usr/bin/env python3
"""Full-build merge keeps exported cancer types, and disease groups stay broad."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tracker.present import apply_classification, broad_sites  # noqa: E402
from tracker.store import TrialStore  # noqa: E402


def main():
    if broad_sites(["Prostate"]) != ["GU"]:
        raise SystemExit("prostate did not stay under GU")
    if broad_sites(["Liver"]) != ["GI"]:
        raise SystemExit("liver did not stay under GI")
    store = TrialStore()
    store.upsert({
        "nct": "NCT00001111",
        "protocols": ["A032102"],
        "name": "A032102",
        "title": "PREDICT",
        "cancer_types": ["Prostate", "Liver", "Sarcoma", "NET"],
        "reviewed": {
            "cancer_types": ["Prostate", "Liver", "Sarcoma", "NET"],
            "anatomy": ["keep-me"],
            "burden": ["Other"],
        },
    }, "ctsu")
    store.upsert({
        "nct": "NCT00001111",
        "protocols": ["A032102"],
        "title": "PREDICT updated",
        "registry": True,
        "reviewed": {"intent": "not_classified"},
    }, "ctgov")
    trial = store.trials[0]
    if trial["cancer_types"] != ["Prostate", "Liver", "Sarcoma", "NET"]:
        raise SystemExit("merge dropped cancer types: %s" % trial["cancer_types"])
    if trial["reviewed"].get("anatomy") != ["keep-me"]:
        raise SystemExit("merge erased unrelated reviewed anatomy")
    if trial["reviewed"].get("intent") != "not_classified":
        raise SystemExit("merge did not keep the later reviewed intent")
    apply_classification(trial)
    if trial["cancer_types"] != ["Prostate", "Liver", "Sarcoma", "NET"]:
        raise SystemExit("classification replaced the reviewed array: %s" % trial["cancer_types"])
    if "GU" not in trial["disease_sites"] or "Prostate" in trial["disease_sites"]:
        raise SystemExit("disease sites stayed fine-grained: %s" % trial["disease_sites"])
    print("cancer merge ok")


if __name__ == "__main__":
    main()
