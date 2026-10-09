"""Check the eleven-trial acceptance sample and write the result."""

import json
from pathlib import Path

CHECKS = [
    {
        "label": "SABR-COMET-3",
        "nct": "NCT03862911",
        "pipeline": "ctgov",
        "forbid_nct": "NCT01446744",
        "why": "ClinicalTrials.gov catch-all. The structured phase is not applicable and the official title says phase III, so the phase-NA radiotherapy query has to keep it.",
    },
    {
        "label": "SABR-COMET-10",
        "nct": "NCT03721341",
        "pipeline": "ctgov",
        "why": "ClinicalTrials.gov phase 3 interventional radiotherapy query.",
    },
    {
        "label": "SABR-SYNC",
        "nct": "NCT05717166",
        "pipeline": "ctgov",
        "why": "ClinicalTrials.gov phase 3 interventional radiotherapy query.",
    },
    {
        "label": "SIMPLIFY-SABR-COMET",
        "nct": "NCT05784428",
        "pipeline": "ctgov",
        "why": "ClinicalTrials.gov phase is not applicable. The randomized phase-NA query has to keep it because the design text says phase III.",
    },
    {
        "label": "SARON",
        "nct": "NCT02417662",
        "pipeline": "rttqa",
        "why": "UK RTTQA portfolio, then ISRCTN (no record) and a ClinicalTrials.gov acronym search. It must not enter only because the NCT was typed into the acceptance list.",
    },
    {
        "label": "CURB-2",
        "nct": "NCT06686771",
        "pipeline": "ctsu",
        "why": "CTSU port of CCTG BR.38. CCTG and ClinicalTrials.gov must merge into this row.",
    },
    {
        "label": "LONESTAR",
        "nct": "NCT03391869",
        "pipeline": "ctgov",
        "why": "ClinicalTrials.gov phase 3 interventional radiotherapy query. A completed or closed single-center trial still belongs.",
    },
    {
        "label": "PRIME-LUNG",
        "nct": "NCT07135882",
        "pipeline": "trog",
        "forbid_nct": "NCT05222087",
        "why": "TROG trial pages, before the ClinicalTrials.gov catch-all. Do not merge the Peter Mac pilot.",
    },
    {
        "label": "TOURIST PRINCE",
        "isrctn": "ISRCTN17123858",
        "pipeline": "rttqa",
        "why": "RTTQA TOURIST page, split into its own ISRCTN row.",
    },
    {
        "label": "TOURIST QUARTZ LUNG",
        "isrctn": "ISRCTN52137148",
        "pipeline": "rttqa",
        "why": "RTTQA TOURIST page, separate from PRINCE. A one-time recruitment figure stays reported-once.",
    },
    {
        "label": "ARREST-2",
        "nct": "NCT05508464",
        "pipeline": "ctgov",
        "why": "ClinicalTrials.gov. Structured phase is not applicable and the title says phase II/III, so the phase-NA query has to keep it.",
    },
]


def _find(trials, check):
    hits = []
    for trial in trials:
        if check.get("nct") and trial.get("nct") == check["nct"]:
            hits.append(trial)
        elif check.get("isrctn") and trial.get("isrctn") == check["isrctn"]:
            hits.append(trial)
    return hits


def _line(trial):
    accrual = trial.get("accrual") or {}
    quality = accrual.get("quality")
    if quality in {"snapshot", "sponsor-posted"}:
        number = accrual.get("actual")
    elif quality == "reported-once":
        number = (accrual.get("reported") or {}).get("n")
    elif quality == "target-only":
        number = accrual.get("target")
    else:
        number = None
    extra = ""
    if accrual.get("reported") and accrual.get("quality") != "reported-once":
        extra = f"; reported-once {accrual['reported'].get('n')} ({accrual['reported'].get('date')})"
    return {
        "name": trial.get("name"),
        "registry": trial.get("nct") or trial.get("isrctn"),
        "groups": ", ".join(trial.get("groups") or []),
        "phase": trial.get("phase"),
        "phase_in_title": trial.get("phase_in_title"),
        "status": trial.get("status"),
        "registry_status": trial.get("registry_status"),
        "accrual": number,
        "target": accrual.get("target"),
        "quality": accrual.get("quality"),
        "reported_note": extra,
        "endpoint": "; ".join(trial.get("primary_endpoints") or []),
        "pipeline": trial.get("source_pipeline"),
        "pipelines": trial.get("pipelines"),
        "disagreements": trial.get("disagreements") or [],
        "notes": (accrual.get("notes") or [])[:4],
    }


def run(trials, out_path):
    lines = ["# Acceptance sample", ""]
    failed = []
    for check in CHECKS:
        hits = _find(trials, check)
        lines.append(f"## {check['label']}")
        if not hits:
            failed.append(check["label"])
            lines.append(f"ABSENT. The {check['pipeline']} pipeline should have brought this in. {check['why']}")
            lines.append("")
            continue
        if len(hits) > 1:
            failed.append(check["label"])
            lines.append(f"DUPLICATE ROWS: {len(hits)}")
        if check.get("forbid_nct"):
            for trial in hits:
                if trial.get("nct") == check["forbid_nct"]:
                    failed.append(check["label"])
                    lines.append(f"Merged with the forbidden id {check['forbid_nct']}.")
        for trial in hits:
            if trial.get("source_pipeline") != check["pipeline"]:
                failed.append(check["label"])
                lines.append(
                    f"Pipeline is {trial.get('source_pipeline')}, expected {check['pipeline']}. {check['why']}"
                )
            info = _line(trial)
            lines.append(f"- Name: {info['name']}")
            lines.append(f"- Registry: {info['registry']}")
            lines.append(f"- Group: {info['groups']}")
            phase = info["phase"] or "Not stated"
            if info["phase_in_title"] and info["phase_in_title"] != info["phase"]:
                phase += f"; record text says phase {info['phase_in_title']}"
            lines.append(f"- Phase: {phase}")
            status = info["status"]
            if info["registry_status"] and info["registry_status"] != info["status"]:
                status += f"; registry status {info['registry_status']}"
            lines.append(f"- Status: {status}")
            lines.append(f"- Accrual: {info['accrual']} ({info['quality']}) target {info['target']}{info['reported_note']}")
            lines.append(f"- Primary endpoint: {info['endpoint'] or 'Not stated'}")
            lines.append(f"- Source pipeline: {info['pipeline']} (also {', '.join(info['pipelines'] or [])})")
            for note in info["notes"]:
                lines.append(f"- Note: {note}")
            for item in info["disagreements"]:
                lines.append(f"- Disagreement ({item.get('field')}): registry value: {item.get('registry_value')} {item.get('other_source')}: {item.get('other_value')}")
        lines.append("")
    # Pilot must remain its own row if present, and must not be the PRIME row.
    prime = _find(trials, {"nct": "NCT07135882"})
    pilot = _find(trials, {"nct": "NCT05222087"})
    if prime and pilot and prime[0] is pilot[0]:
        failed.append("PRIME-LUNG merged with Peter Mac pilot")
        lines.append("PRIME-LUNG and NCT05222087 are the same row.")
    text = "\n".join(lines)
    if failed:
        text += "\nFailed: " + ", ".join(dict.fromkeys(failed)) + "\n"
    else:
        text += "\nAll eleven sample trials are present as their own rows.\n"
    Path(out_path).write_text(text)
    return failed, text
