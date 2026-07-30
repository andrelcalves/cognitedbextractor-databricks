# Product Requirements Document (PRD)

## Product
Databricks → Cognite Everest DB Extractor

## Purpose
Extract SAP Everest views from Databricks (`hub_dev.g_external.v_cognite_*_everest`) into Cognite Raw tables (`db_databricks_everest_raw.tb_*`) with stable primary keys and controlled data volume.

## File layout
| File | Role |
|------|------|
| `sap/config.yaml` | Extract config (149 categorized queries), blanked connection endpoint |
| `sap/prompt.md` | Authoring instructions for YAML merges |
| `sap/LOAD-STRATEGY.md` | Volume tiers per view and proposed load strategy |
| `sap/PRD.md` | This document — extract rules |

---

## Rules

### R1 — Primary keys = MANDT + SAP key fields
- Every extract query MUST define a Cognite `primary-key` based on the SAP table primary key, **including `MANDT` as the first component**.
- All queries emit a synthetic key: `CONCAT(MANDT, '_', field1, '_', ...)` AS `{TABLE}_PK`.
- Cognite field: `primary-key: "{TABLE}_PK"`.
- Key field choices follow the SAP table reference. Tables whose client field is named `CLIENT` instead of `MANDT` (e.g. `T003P`) use `CLIENT` as the first component.
- Where the SAP reference differs between ECC and S/4HANA, prefer the key fields that exist in both releases (e.g. `VBFA` uses `VBELV, POSNV, VBELN, POSNN, VBTYP_N` rather than the S/4-only `RUUID`).
- When the Databricks view renames a key column, the PK MUST use the **view column name**, not the classic SAP dictionary name. Known cases:
  - Client field `MANDANT` instead of `MANDT`: `QALS`, `QAMR`, `QAVE`, `QPGT`
  - `MHIO`: use `WPPOS` (not `WAPOS`); do **not** include `LFDAT` (not a key / not in the view)
  - `QAMR`: do **not** include `DETAILERG` (not a key / not in the view)
  - `PMCO`: use `PERBL` (not `PERIO`); full SAP key without `F_OBJNR` — `MANDT, OBJNR, COCUR, BELTP, WRTTP, GJAHR, ACPOS, VERSN, PERBL, VORGA, BEMOT, ABKAT`

### R2 — Rolling 1-year filter on DATETIMESTAMP
- Extract queries for Tiers 2–5 and the unmeasured views (98 tables) MUST filter to the last year of data. Tier 1 is exempt — see R2c.
- Use a rolling window relative to extraction time:
  - `current_timestamp() - INTERVAL 1 YEAR`
- Filter field: `DATETIMESTAMP`.
- `DATETIMESTAMP` may appear in either format:
  - with fractional seconds: `2026-07-14 05:40:39.340502`
  - without fractional seconds: `2024-11-18 17:57:50`
- Queries MUST normalize both formats before comparison using:

```sql
WHERE coalesce(
        try_to_timestamp(DATETIMESTAMP, 'yyyy-MM-dd HH:mm:ss.SSSSSS'),
        try_to_timestamp(DATETIMESTAMP, 'yyyy-MM-dd HH:mm:ss')
      ) >= current_timestamp() - INTERVAL 1 YEAR
```

- Rows with null or unparseable `DATETIMESTAMP` MUST be excluded.

### R2b — Temporary 10-row cap (availability validation)
- The 1-year window alone still exceeds acceptable volume on the largest tables.
- While validating that every view is reachable in Databricks, queries end with `LIMIT 10`.
- Exception: the 51 Tier 1 tables (under 1 million rows, see `LOAD-STRATEGY.md`) carry no cap. They are small enough to load in full, so the cap suppressed data without saving meaningful time. 98 of the 149 queries still carry `LIMIT 10`.
- Where present, the cap makes the extract a connectivity/schema smoke test, **not** a usable data load, and MUST be removed before any production extract run.
- Note: `LIMIT` is applied after the `WHERE` clause, so the source table is still fully scanned. If scan cost becomes the blocker, drop the `WHERE` clause for the smoke test instead.

### R2c — Tier 1 loads full, unfiltered
- The 51 Tier 1 tables (under 1 million rows, see `LOAD-STRATEGY.md`) MUST NOT carry the R2 filter. Their queries are `SELECT ... FROM ...` with no `WHERE` clause.
- Rationale: the tier totals 5.6 million rows, so a full reload is cheap. More importantly, these are mostly customizing and master data tables that rarely change, so a 1-year window on `DATETIMESTAMP` silently returns few or zero rows — `IFLOS` returned 0 rows in the 2026-07-27 run for exactly this reason.
- A table moving out of Tier 1 after a re-count MUST have the R2 filter reinstated.

### R3 — Query naming, destinations, and categorization
- Query name pattern: `extract-everest-{TABLE}`
- Destination database: `db_databricks_everest_raw`
- Destination table pattern: `tb_{TABLE}`
- Source view pattern: `hub_dev.g_external.v_cognite_{table_lower}_everest`
- Queries MUST be grouped by SAP functional area with paired category comments (header + end).
- Each query MUST have a one-line `# TABLE - description [TIER n | count linhas]` comment immediately above it. The tier and count come from `sap/LOAD-STRATEGY.md`; views with no measured count use `[TIER ? | sem contagem]`.

### R4 — Blanked connection endpoint in committed configs
- Committed YAML MUST NOT contain live Databricks `Host` or `HTTPPath` values.
- Use blank placeholders (`Host=;`, `HTTPPath=;`) as in `sap/config.yaml`.
- Cognite `tenant`, `client-id`, `secret`, and ODBC `PWD` remain empty in git; fill locally or via secrets at runtime.

---

## Example query shape

```sql
SELECT CONCAT(MANDT, '_', WEBLNR, '_', WEBLPOS) AS AFFW_PK, *
FROM hub_dev.g_external.v_cognite_affw_everest
WHERE coalesce(
        try_to_timestamp(DATETIMESTAMP, 'yyyy-MM-dd HH:mm:ss.SSSSSS'),
        try_to_timestamp(DATETIMESTAMP, 'yyyy-MM-dd HH:mm:ss')
      ) >= current_timestamp() - INTERVAL 1 YEAR
LIMIT 10
```

---

## Out of scope
- Runtime credential injection (handled outside git)
- Incremental watermarking beyond the rolling 1-year window
- Per-table exceptions to R2 beyond the Tier 1 exemption in R2c
