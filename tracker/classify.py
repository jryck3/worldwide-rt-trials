"""Radiotherapy role classification.

RT-focused: radiotherapy is named in the title, or it is the randomized question.
RT component: protocol-specified external-beam or brachytherapy that is not the randomized question.
Radiopharmaceutical: a therapeutic isotope without external-beam or brachytherapy radiotherapy.
Imaging tracers are excluded.
"""

import re

RT_TERMS = re.compile(
    r"\b(radiation|radiotherap\w*|chemoradi\w*|radiochemo\w*|immunoradi\w*|irradiat\w*|"
    r"reirradiat\w*|SBRT|SABR|SRS|stereotactic|radiosurg\w*|proton(?!\s*pump)|brachytherap\w*|"
    r"IMRT|VMAT|hypofractionat\w*|craniospinal|whole[- ]brain|\bRT\b)\b",
    re.I,
)
BRACHY = re.compile(r"\b(brachytherap\w*|intracavitary|interstitial\s+implant)\b", re.I)
RADIOPHARM = re.compile(
    r"(lutetium|\bLu[- ]?177\b|\b177[- ]?Lu|pluvicto|lutathera|"
    r"radium|\bRa[- ]?223\b|xofigo|actinium|\bAc[- ]?225\b|"
    r"yttrium[- ]?90|\bY[- ]?90\b|therasphere|sir[- ]?spheres|"
    r"thorium|\bPb[- ]?212\b|astatine|iobenguane|131I[- ]?MIBG|\bI[- ]?131\b|radioiodine|"
    r"radioligand therap\w*|radioemboli\w*|PSMA[- ]?617|\bSIRT\b|radioimmunotherap\w*|"
    r"ibritumomab|zevalin|tositumomab|bexxar|samarium|\bSm[- ]?153\b)",
    re.I,
)
IMAGING = re.compile(
    r"(tomograph|\bPET\b|\bCT\b|x-?ray|radiograph|scintigraph|bone scan|imaging|mammogra|"
    r"\bDXA\b|\bDEXA\b|MUGA|SPECT|\bscan\b|fluciclovine|piflufolastat|gallium|\bGa[- ]?68\b|"
    r"\bF[- ]?18\b|fluorine|FDG|copper Cu|zirconium)",
    re.I,
)
PRIOR_RT = re.compile(
    r"\b(prior|previous|history of|previously treated with|after)\b[^.]{0,40}\b"
    r"(radiation|radiotherapy|irradiation|SBRT|SABR|SRS)\b",
    re.I,
)
PHASE_LANGUAGE = re.compile(
    r"\bphase\s*(i{1,3}\s*/\s*i{1,3}|[123]\s*/\s*[123]|i{1,3}|[123])\b",
    re.I,
)


def _arm_has_rt(arm):
    text = " ".join(filter(None, [arm.get("label"), arm.get("description")]))
    if IMAGING.search(text) and not RT_TERMS.search(text):
        return False
    if RADIOPHARM.search(text) and not RT_TERMS.search(text):
        return False
    return bool(RT_TERMS.search(text))


def modality_of(has_ebrt, has_brachy, has_radio):
    kinds = []
    if has_ebrt:
        kinds.append("external beam")
    if has_brachy:
        kinds.append("brachytherapy")
    if has_radio:
        kinds.append("radiopharmaceutical")
    if len(kinds) > 1:
        return "mixed"
    return kinds[0] if kinds else None


def classify_record(titles, interventions=None, arms=None, allocation=None, extra_text=""):
    """Return (role, evidence, modality) or (None, evidence, None)."""
    interventions = interventions or []
    arms = arms or []
    title_blob = " | ".join(filter(None, titles))
    evidence = []
    title_rt = RT_TERMS.search(title_blob)
    if title_rt:
        evidence.append(f"title: {title_rt.group(0)}")

    rt_iv, radiopharm_iv, brachy_iv = [], [], []
    for iv in interventions:
        name = iv.get("name") or ""
        names = " ".join([name] + list(iv.get("otherNames") or []))
        if RADIOPHARM.search(names):
            radiopharm_iv.append(name)
        elif IMAGING.search(names) and not RT_TERMS.search(name):
            continue
        elif iv.get("type") == "RADIATION" or RT_TERMS.search(name):
            rt_iv.append(name)
            if BRACHY.search(names):
                brachy_iv.append(name)
    if rt_iv:
        evidence.append("intervention: " + "; ".join(sorted(set(rt_iv))))
    if radiopharm_iv:
        evidence.append("radiopharmaceutical: " + "; ".join(sorted(set(radiopharm_iv))))

    rt_arms = [a.get("label") or "" for a in arms if _arm_has_rt(a)]
    non_rt_arms = [a.get("label") or "" for a in arms if a.get("label") and not _arm_has_rt(a)]
    randomized = (allocation or "").upper() == "RANDOMIZED" or bool(re.search(r"randomi[sz]", extra_text or "", re.I))
    randomized_question = bool(randomized and rt_arms and non_rt_arms and (rt_iv or title_rt or rt_arms))
    if randomized_question:
        evidence.append("randomized question: radiotherapy is not given on every arm")

    has_brachy = bool(brachy_iv) or bool(BRACHY.search(title_blob))
    has_radio = bool(radiopharm_iv) or bool(RADIOPHARM.search(title_blob))
    has_ebrt = any(not BRACHY.search(name) for name in rt_iv) or bool(title_rt and not has_brachy and not has_radio)
    if title_rt and BRACHY.search(title_rt.group(0)):
        has_brachy = True
        has_ebrt = any(not BRACHY.search(name) for name in rt_iv)
    modality = modality_of(has_ebrt, has_brachy, has_radio)

    if (radiopharm_iv or RADIOPHARM.search(title_blob)) and not rt_iv and not title_rt:
        return "Radiopharmaceutical", evidence, "radiopharmaceutical"
    if title_rt or randomized_question:
        role_modality = modality or "external beam"
        if role_modality == "radiopharmaceutical" and rt_iv:
            role_modality = "mixed"
        return "RT-focused", evidence, role_modality if role_modality != "radiopharmaceutical" else "external beam"
    if rt_iv:
        return "RT component", evidence, modality or "external beam"
    if rt_arms and not (title_rt or rt_iv):
        evidence.append("arm text only: " + "; ".join(rt_arms[:3]))
        return None, evidence, None
    return None, evidence, None


def classify_text(title, body="", treatments=None, randomized=False):
    """Classification when the only inputs are prose and a treatment list."""
    treatments = treatments or []
    interventions = []
    for name in treatments:
        kind = "RADIATION" if RT_TERMS.search(name) or BRACHY.search(name) else "OTHER"
        if RADIOPHARM.search(name):
            kind = "OTHER"
        interventions.append({"name": name, "type": kind})
    cleaned = PRIOR_RT.sub(" ", body or "")
    # Body RT that survives prior-RT stripping can support component classification
    # when a treatment list is absent.
    if RT_TERMS.search(cleaned) and not treatments:
        interventions.append({"name": "radiotherapy described in protocol text", "type": "RADIATION"})
    arms = []
    if randomized and RT_TERMS.search(title or "") is None and re.search(r"\bversus\b|\bvs\.?\b|with or without", f"{title} {cleaned}", re.I):
        if RT_TERMS.search(cleaned) or any(RT_TERMS.search(t) for t in treatments):
            arms = [
                {"label": "Radiotherapy arm", "description": "radiotherapy"},
                {"label": "Control arm", "description": "no radiotherapy"},
            ]
    return classify_record([title or ""], interventions, arms, "RANDOMIZED" if randomized else None, cleaned)


def phase_language(text):
    """Return a normalized phase mentioned in prose, or None."""
    if not text:
        return None
    found = []
    for match in PHASE_LANGUAGE.finditer(text):
        token = match.group(1).upper().replace(" ", "")
        mapping = {
            "I": "1", "II": "2", "III": "3", "1": "1", "2": "2", "3": "3",
            "I/II": "1/2", "II/III": "2/3", "1/2": "1/2", "2/3": "2/3",
        }
        found.append(mapping.get(token, token))
    if "2/3" in found:
        return "2/3"
    if "3" in found and "2" in found:
        return "2/3"
    if "3" in found:
        return "3"
    if "2" in found:
        return "2"
    if "1/2" in found:
        return "1/2"
    if "1" in found:
        return "1"
    return None
