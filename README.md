# Worldwide RT Trials

Local tracker of phase 2, phase 2/3, and phase 3 interventional trials in which radiotherapy is a randomized or protocol-specified treatment. It is for finding trials to enroll to and for seeing what is accruing. Closed and completed trials stay in the database.

This project does not modify the CTSU tracker. The CTSU rows here are a read of that project's public export. The repository is https://github.com/jryck3/worldwide-rt-trials. The public page is https://jryck3.github.io/worldwide-rt-trials/.

Open `dashboard/index.html` in a browser, or use the public page. The trial table is embedded in that file, and the same file is published from `docs/index.html`. `data/trials.json` is the same payload. There are 5,470 trials.

The opening view is phase 2, phase 2/3, and phase 3, status Recruiting, every RT role, every accrual quality, every disease site, and every source. Each filter row has an All box. My trials is saved in the browser. Sign in with Gmail works when the page is opened from localhost, and that list is stored separately from the CTSU tracker.

NRG, SWOG, and ECOG-ACRIN studies are named with the protocol number first, then the acronym: NRG-GU015 (ARCHER), S2427 (BRIGHT), EA2182 (DECREASE). The source row is the cooperative group or the pipeline that is not already covered by a group. CTSU is not a source checkbox. Every CTSU radiotherapy study is NRG, SWOG, ECOG-ACRIN, ALLIANCE, CCTG, COG, ETCTN, or PEP-CTN. The table column Brought in by still says which pipeline created the row.

An opened NCI study with a protocol on file shows the same kind of brief as the CTSU tracker: amendments, schema, eligibility, assessments, study calendar, specimens, and data submission, including TRIAD when that protocol requires it. Other trials show the ClinicalTrials.gov arm list. A full calendar, specimen table, or image-submission rule is included only when it was read from a protocol. It is not invented.

## Accrual quality

Every row has one accrual-quality flag. A patients-per-month figure is calculated only for a snapshot actual.

| Flag | Meaning |
|---|---|
| Snapshot | An enrolled count from a feed that can be snapshotted again (CTSU, the KROG protocol board, a TROG page that fills in current accrual) |
| Sponsor-posted | ClinicalTrials.gov enrollment marked Actual, kept with the registry update date. Not a daily feed |
| Target only | Planned sample size and no public numerator. An Estimated ClinicalTrials.gov enrollment is a target |
| Reported once | A number from a paper, abstract, or news post, stored with the source and date |
| Status only | Open, closed, or not yet recruiting, with no number |

ISRCTN `totalFinalEnrolment` is not stored as accrual. EORTC does not publish a running enrolled count.

## Sources

The first pipeline that creates a row is the source. Later pipelines add registry fields to that row instead of making a second one.

1. CTSU public export. RT-related studies only, including the RT role, protocol number, NCT, lead group, phase, status, actual accrual, and target. The 8 October 2026 export had 254 studies, of which 57 were RT-related.
2. EORTC Prisma API, including E2-RADIatE. OligoCare (EORTC 1822) and ReCare (EORTC 2011) are cohort rows. SPRINT (EORTC 2387) is its own trial, linked to OligoCare.
3. UK RTTQA portfolio, resolved in the ISRCTN API. SARON has no ISRCTN record, so that page is resolved by a ClinicalTrials.gov acronym search.
4. TROG trial pages. ANZCTR is kept when a TROG study has no NCT.
5. CCTG public trial list. Studies already ported from CTSU are merged onto the existing row.
6. KROG protocol board. Enrollment is labeled as Korea.
7. ClinicalTrials.gov v2, for phase 2, phase 2/3, and phase 3 interventional trials with a radiotherapy intervention, plus phase-not-applicable records whose own text describes a phase 2 or 3 trial. This is the catch-all for groups that are not scraped separately, including JCOG, GORTEC, DAHANCA, DBCG, SAKK, and Cancer Trials Ireland. Those names are tags when the record identifies them.

One row per trial. Matching is NCT, then ISRCTN, then a group protocol number. A TROG site of an NRG trial, an EORTC collaboration, and a CCTG trial already on CTSU stay on one row, with every group name kept. Each candidate inside a platform (TOURIST PRINCE and QUARTZ LUNG, for example) is its own row.

The EU CTIS public search API returned HTTP 403 (missing authentication). Country recruiting status comes from ClinicalTrials.gov locations, EORTC centers, and ISRCTN. A missing location status is not treated as recruiting in every listed country. JCOG trials are labeled as Japan only. KROG enrollment is labeled as Korea.

## RT classification

RT-focused means radiotherapy is named in the title or is the randomized question. RT component means protocol-specified external-beam or brachytherapy radiotherapy that is not the randomized question. Radiopharmaceuticals are a separate role and are included when RT role All is selected. Imaging tracers are excluded. Phase 1 studies stay in the file and are hidden until that phase box is checked.

When a registry field disagrees with a group page or with an earlier description, the registry phase, status, population, and primary endpoint are kept, and the disagreement is stored with the date checked.

## Rebuild

From this directory:

```bash
python3 build.py
```

`--refresh` ignores cached downloads. Cached pages live in `data/cache/`. The build reads the CTSU export and does not write into that project. `data/acceptance.md` checks eleven metastatic and oligometastatic trials after each build.
