# Product Requirements Document (PRD)

## Product
Databricks → Cognite SAP Everest DB Extractor (3 continuous instances)

## Purpose
Extract SAP Everest views from Databricks (`hub_dev.g_external.v_cognite_*_everest`) into Cognite RAW (`db_databricks_glb_raw.tb_*Everest`) with stable SHA-256 GUID row keys (`primary-key: "{key}"`), incremental load from **2026-08-01**, and a **15-minute** continuous schedule across three balanced extractors.

**Prerequisite:** data through July is already in Cognite. These configs do **not** re-extract pre-August history.

## File layout
| File | Role |
|------|------|
| `sap/config-extract-1.yaml` | Continuous extractor 1 (~49 queries, includes MDTB) |
| `sap/config-extract-2.yaml` | Continuous extractor 2 (~50 queries, includes COEP) |
| `sap/config-extract-3.yaml` | Continuous extractor 3 (~50 queries, includes EBAN) |
| `sap/config.yaml` | Pointer only — do not run; use the three extract configs |
| `sap/LOAD-STRATEGY.md` | Volume tiers, 3-way split, ops notes |
| `sap/PRD.md` | This document — extract rules |

| Concern | Value |
|---------|--------|
| Extraction pipelines | `ep_databricks_everest_dbExtractor_1` / `_2` / `_3` (create in CDF UI) |
| RAW database | `db_databricks_glb_raw` |
| RAW table pattern | `tb_{Pascal}Everest` (AFFW → `tb_AffwEverest`) |
| State store | RAW `db_extractor_state` / `everest_extract_1\|2\|3` |
| Mode | `continuous` |
| Parallelism | `1` per extractor (serial queue → small before large) |
| Upload queue | `50000` |
| Schedule | `interval` / `15m` on every query |
| Incremental field | `DATETIMESTAMP` |
| Initial start | `2026-08-01 00:00:00` |

Legacy `db_databricks_everest_raw.tb_*` is out of scope (no migration in this change).

---

## Rules

### R1 — Cognite `primary-key` = stable GUID (`key`)
- Every query with RAW destination MUST set `primary-key: "{key}"` ([Cognite DB Extractor docs](https://docs.cognite.com/cdf/integration/guides/extraction/configuration/db)).
- The extractor does **not** mint GUIDs by itself. Each query MUST compute a stable opaque key in SQL and expose it as column `key`.
- Use SHA-256 over the SAP business-key columns (same fields formerly used in `CONCAT`), joined with `'_'`:

```sql
SELECT sha2(concat_ws('_', MANDT, WEBLNR, WEBLPOS), 256) AS key, *
```

- Resulting RAW keys are 64-char hex digests (GUID-style opaque ids), e.g. `b8333adbe03f960bbf9a350a58c84c3002008712e78698bf33d3696a0425596c`.
- Do **not** use `uuid()` / random ids — they change every run and break RAW upserts.
- Do **not** set `primary-key` to raw SAP columns (`"{MANDT}_{MATNR}_..."`).
- Business-key column lists still follow the SAP table reference (including client as first component: `MANDT`, or `CLIENT` / `MANDANT` where the view uses those names). They appear only inside `sha2(concat_ws(...))`, not in the Cognite `primary-key` template.
- Known view column exceptions (inputs to the hash only):
  - Client `MANDANT` instead of `MANDT`: `QALS`, `QAMR`, `QAVE`, `QPGT`
  - `MHIO`: use `WPPOS` (not `WAPOS`); do not include `LFDAT`
  - `QAMR`: do not include `DETAILERG`
  - `PMCO`: use `PERBL` (not `PERIO`); full SAP key without `F_OBJNR`
  - Prefer keys that exist in both ECC and S/4 where they diverge (e.g. `VBFA` without S/4-only `RUUID`)
  - Tables whose client field is `CLIENT` (e.g. `T003P`) use `CLIENT` as the first component

### R2 — Incremental watermark on DATETIMESTAMP (all tiers)
- All 149 queries (Tiers 1–5 and `?`) MUST use incremental load:
  - `incremental-field: DATETIMESTAMP`
  - `initial-start: "2026-08-01 00:00:00"`
  - SQL: `WHERE {incremental_field} >= '{start_at}' ORDER BY {incremental_field} ASC`
- First run per query: catch-up from August 2026 until the watermark advances.
- Subsequent runs: only rows at/after the stored state.
- A `state-store` is required (RAW preferred for durability across hosts).
- No rolling 1-year window and no `LIMIT 10` in production Everest configs.
- Tier 1 is also incremental (not an unfiltered full reload every 15 minutes).

`DATETIMESTAMP` formats in source (string, zero-padded — lexicographic order matches time order):

- with fractional seconds: `2026-07-14 05:40:39.340502`
- without: `2024-11-18 17:57:50`

Null / unparseable values fail `>=` and are excluded.

### R3 — Naming, destinations, categorization
- Query name: `extract-everest-{TABLE}`
- Source view: `hub_dev.g_external.v_cognite_{table_lower}_everest`
- Destination database: `db_databricks_glb_raw`
- Destination table: `tb_{Pascal}Everest` where `Pascal` = first character uppercased, rest lowercased (`AFFW` → `Affw`, `TC25T` → `Tc25t`, `COEP` → `Coep`)
- Each query MUST have a one-line `# TABLE - description [TIER n | count]` comment
- Queries are ordered by tier (Tier 1 → `?` → 2 → 3 → 4 → 5), ascending measured size within the tier

### R4 — Three extractors, balanced per tier
- Exactly three config files / three extraction pipelines / three state tables
- Assignment: for each tier in order `5,4,3,2,1,?`, sort by measured rows descending; assign to the extractor with fewest tables **in that tier**, then lowest total measured rows
- Tier 5 split (mandatory): Extractor 1 = `MDTB`, Extractor 2 = `COEP`, Extractor 3 = `EBAN`
- Inside each file, `parallelism: 1` plus ascending order ensures small tables finish before large ones start **in that process**

### R5 — Schedule and continuous mode
- `extractor.mode: continuous`
- Every query: `schedule: { type: interval, expression: 15m }`
- During catch-up, effective cadence stretches until watermarks catch up (do not schedule tighter than previous run duration)
- Optional later: longer intervals only for Tier 4/5 if overlap persists after catch-up

### R6 — Secrets in git
- Committed YAML MUST blank Databricks `Host` / `HTTPPath`, Cognite `tenant` / `client-id` / `secret`, and ODBC `PWD`

---

## Example query shape

```yaml
  - name: "extract-everest-AFFW"
    database: "db-databricks-raw"
    query: >
      SELECT sha2(concat_ws('_', MANDT, WEBLNR, WEBLPOS), 256) AS key, *
      FROM hub_dev.g_external.v_cognite_affw_everest
      WHERE {incremental_field} >= '{start_at}'
      ORDER BY {incremental_field} ASC
    incremental-field: DATETIMESTAMP
    initial-start: "2026-08-01 00:00:00"
    schedule:
      type: interval
      expression: 15m
    destination:
      type: "raw"
      database: "db_databricks_glb_raw"
      table: "tb_AffwEverest"
    primary-key: "{key}"
```

---

## Out of scope
- Runtime credential injection (handled outside git)
- JDE IN / JDE NA
- Column projection for Tiers 4/5
- Monthly historical backfill windows
- Creating the three CDF Extraction Pipeline objects in the Cognite UI (manual prerequisite)
- Migrating rows from `db_databricks_everest_raw` → `db_databricks_glb_raw`
