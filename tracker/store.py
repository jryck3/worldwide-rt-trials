"""One row per trial. Match NCT, then ISRCTN, then a group protocol number."""

import re

from .model import (
    accrual_quality_rank, add_unique, apply_accrual, blank_trial, choose_id,
    disagreement, norm_isrctn, norm_nct, protocol_keys,
)


_RT_ROLES = {"RT-focused", "RT component", "Radiopharmaceutical"}
_NON_RT_ROLES = {
    "Systemic therapy",
    "Cancer control",
    "Transplant / cellular therapy",
    "Surgery",
    "Imaging",
    "Registry / biospecimen",
}


def _later_rt_upgrade(trial, record):
    """A later pipeline found radiotherapy after CTSU stored a non-RT role."""
    if trial.get("rt_role") not in _NON_RT_ROLES or record.get("rt_role") not in _RT_ROLES:
        return False
    # A manual CTSU classification already rejected a misleading registry mention.
    if any(str(item).startswith("manual:") for item in (trial.get("rt_evidence") or [])):
        return False
    return True


def _registry_name_wins(current, incoming):
    if not incoming:
        return False
    if not current:
        return True
    if current.casefold() == incoming.casefold():
        return current != incoming
    org_id = bool(re.fullmatch(r"(?:NCI-)?\d[\w./-]*", current)) and not re.search(r"[A-Za-z]{4,}", current)
    page_heading = len(current) >= 36 or " - " in current
    incoming_ok = len(incoming) <= 40 and re.search(r"[A-Za-z]{3,}", incoming)
    return bool(incoming_ok and (org_id or page_heading))


class TrialStore:
    def __init__(self):
        self.trials = []
        self._nct = {}
        self._isrctn = {}
        self._protocol = {}

    def find(self, record):
        nct = norm_nct(record.get("nct"))
        if nct and nct in self._nct:
            return self._nct[nct]
        isrctn = norm_isrctn(record.get("isrctn"))
        if isrctn and isrctn in self._isrctn:
            return self._isrctn[isrctn]
        for key in self._keys(record):
            if key in self._protocol:
                return self._protocol[key]
        return None

    def upsert(self, record, pipeline):
        existing = self.find(record)
        if existing is None:
            trial = blank_trial()
            self._merge(trial, record, pipeline, initial=True)
            trial["source_pipeline"] = pipeline
            self.trials.append(trial)
            self._index(trial)
            return trial, False
        self._merge(existing, record, pipeline, initial=False)
        self._index(existing)
        return existing, True

    def _keys(self, record):
        keys = set()
        for protocol in record.get("protocols") or []:
            keys |= protocol_keys(protocol)
        keys |= protocol_keys(record.get("name"))
        return keys

    def _index(self, trial):
        if trial.get("nct"):
            self._nct[trial["nct"]] = trial
        if trial.get("isrctn"):
            self._isrctn[trial["isrctn"]] = trial
        for protocol in trial.get("protocols") or []:
            for key in protocol_keys(protocol):
                self._protocol.setdefault(key, trial)
        for key in protocol_keys(trial.get("name")):
            self._protocol.setdefault(key, trial)

    def _merge(self, trial, record, pipeline, initial):
        if pipeline not in trial["pipelines"]:
            trial["pipelines"].append(pipeline)
        nct = norm_nct(record.get("nct"))
        if nct:
            trial["nct"] = nct
        isrctn = norm_isrctn(record.get("isrctn"))
        if isrctn:
            trial["isrctn"] = isrctn
        if record.get("eudract") and not trial.get("eudract"):
            trial["eudract"] = record["eudract"]
        for protocol in record.get("protocols") or []:
            add_unique(trial["protocols"], protocol)
        for group in record.get("groups") or []:
            add_unique(trial["groups"], group)
        if record.get("lead_group") and not trial.get("lead_group"):
            trial["lead_group"] = record["lead_group"]
        if record.get("sponsor") and (initial or record.get("registry") or not trial.get("sponsor")):
            trial["sponsor"] = record["sponsor"]
        for name in record.get("collaborators") or []:
            add_unique(trial["collaborators"], name)
        if record.get("name") and (initial or not trial.get("name") or (record.get("registry") and _registry_name_wins(trial.get("name"), record["name"]))):
            trial["name"] = record["name"]
        if record.get("title") and (initial or not trial.get("title") or record.get("registry")):
            trial["title"] = record["title"]
        self._merge_phase(trial, record, initial)
        self._merge_status(trial, record, initial)
        if record.get("population") and (initial or record.get("registry") or not trial.get("population")):
            trial["population"] = record["population"]
        for condition in record.get("conditions") or []:
            add_unique(trial["conditions"], condition)
        if record.get("primary_endpoints") and (record.get("registry") or not trial.get("primary_endpoints")):
            if trial.get("primary_endpoints") and record["primary_endpoints"] != trial["primary_endpoints"] and not initial:
                if record.get("registry"):
                    disagreement(trial, "primary endpoint", "; ".join(record["primary_endpoints"]),
                                 "; ".join(trial["primary_endpoints"]), trial.get("status_source") or "earlier source")
                    trial["primary_endpoints"] = record["primary_endpoints"]
            elif not trial.get("primary_endpoints"):
                trial["primary_endpoints"] = list(record["primary_endpoints"])
        took_role = False
        if record.get("rt_role") and (initial or pipeline == "ctsu" or not trial.get("rt_role")):
            trial["rt_role"] = record["rt_role"]
            took_role = True
        elif _later_rt_upgrade(trial, record):
            # CTSU stored a non-RT role first. A later pipeline can still mark the
            # trial RT-focused, an RT component, or a radiopharmaceutical.
            trial["rt_role"] = record["rt_role"]
            took_role = True
        for item in record.get("rt_evidence") or []:
            add_unique(trial["rt_evidence"], item)
        if record.get("modality") and (initial or not trial.get("modality")) and (took_role or trial.get("rt_role") in _RT_ROLES):
            trial["modality"] = record["modality"]
        if record.get("record_kind") and record["record_kind"] != "trial":
            trial["record_kind"] = record["record_kind"]
        if record.get("platform"):
            trial["platform"] = record["platform"]
        if record.get("study_type"):
            if initial or trial.get("study_type") in (None, "interventional"):
                if record["study_type"] != "interventional" or initial:
                    trial["study_type"] = record["study_type"]
        accrual = record.get("accrual") or {}
        if accrual:
            before = trial["accrual"]["quality"]
            apply_accrual(
                trial,
                accrual.get("quality") or "status-only",
                actual=accrual.get("actual"),
                target=accrual.get("target"),
                as_of=accrual.get("as_of"),
                source=accrual.get("source"),
                source_url=accrual.get("source_url"),
                enrollment_type=accrual.get("enrollment_type"),
                note=None,
                reported=accrual.get("reported"),
            )
            incoming = accrual.get("quality") or "status-only"
            if accrual_quality_rank(incoming) >= accrual_quality_rank(before):
                for note in accrual.get("notes") or []:
                    add_unique(trial["accrual"]["notes"], note)
        for country in record.get("countries_open") or []:
            add_unique(trial["countries_open"], country)
        for country in record.get("countries_listed") or []:
            add_unique(trial["countries_listed"], country)
        if record.get("country_limit") and not trial.get("country_limit"):
            trial["country_limit"] = record["country_limit"]
        for source in record.get("sources") or []:
            if source not in trial["sources"]:
                trial["sources"].append(source)
        for item in record.get("disagreements") or []:
            if item not in trial["disagreements"]:
                trial["disagreements"].append(item)
        for key, url in (record.get("urls") or {}).items():
            trial["urls"].setdefault(key, url)
        if record.get("start_date") and not trial.get("start_date"):
            trial["start_date"] = record["start_date"]
        if record.get("checked_at"):
            trial["checked_at"] = record["checked_at"]
        if record.get("phase_in_title"):
            trial["phase_in_title"] = record["phase_in_title"]
        trial["id"] = choose_id(trial)

    def _merge_phase(self, trial, record, initial):
        incoming = record.get("phase")
        if not incoming:
            if record.get("phase_code") and not trial.get("phase_code"):
                trial["phase_code"] = record["phase_code"]
            return
        if initial or not trial.get("phase"):
            trial["phase"] = incoming
            trial["phase_code"] = record.get("phase_code") or incoming
            return
        if record.get("registry") and incoming != trial.get("phase"):
            disagreement(
                trial, "phase", incoming, trial.get("phase"),
                record.get("phase_other_source") or trial.get("status_source") or "earlier description",
            )
            trial["phase"] = incoming
            trial["phase_code"] = record.get("phase_code") or incoming
        elif not record.get("registry") and incoming != trial.get("phase"):
            disagreement(
                trial, "phase", trial.get("phase"), incoming,
                record.get("phase_other_source") or pipeline_name(record),
            )

    def _merge_status(self, trial, record, initial):
        incoming = record.get("status")
        if not incoming:
            return
        if record.get("registry"):
            if trial.get("registry_status") and trial["registry_status"] != incoming:
                disagreement(trial, "status", incoming, trial["registry_status"], trial.get("status_source"))
            trial["registry_status"] = incoming
            trial["registry_checked"] = record.get("status_checked")
            if initial or not trial.get("status") or trial.get("status_source") == record.get("status_source"):
                trial["status"] = incoming
                trial["status_source"] = record.get("status_source")
                trial["status_checked"] = record.get("status_checked")
            elif trial.get("status") != incoming:
                # Registry is the field of record. Keep a group feed status only when this
                # registry update is older than a daily accrual feed already stored.
                if record.get("prefer_existing_status"):
                    disagreement(trial, "status", incoming, trial.get("status"), trial.get("status_source"))
                else:
                    disagreement(trial, "status", incoming, trial.get("status"), trial.get("status_source"))
                    trial["status"] = incoming
                    trial["status_source"] = record.get("status_source")
                    trial["status_checked"] = record.get("status_checked")
            return
        if initial or not trial.get("status"):
            trial["status"] = incoming
            trial["status_source"] = record.get("status_source")
            trial["status_checked"] = record.get("status_checked")
        elif incoming != trial.get("status") and not trial.get("registry_status"):
            disagreement(trial, "status", trial.get("status"), incoming, record.get("status_source"))


def pipeline_name(record):
    sources = record.get("sources") or []
    if sources:
        return sources[-1].get("pipeline")
    return "secondary description"
