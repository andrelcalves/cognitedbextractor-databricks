# Product Requirements Document (PRD)

## Product
Databricks → Cognite Everest DB Extractor

## Purpose
Extract SAP Everest views from Databricks (`hub_dev.g_external.v_cognite_*_everest`) into Cognite Raw tables (`db_databricks_everest_raw.tb_*`) with stable primary keys and controlled data volume.

## File layout
| File | Role |
|------|------|
| `sap/config.yaml` | Small template (10 queries), blanked connection endpoint |
| `sap/config-new.yaml` | Earlier 57-query categorized draft |
| `sap/config-full.yaml` | Full 149-query extract config (source of truth for complete extract) |
| `sap/prompt.md` | Authoring instructions for YAML merges |
| `sap/queries.txt` | Source table inventory / notes |
| `sap/PRD.md` | This document — extract rules |

---

## Rules

### R1 — Primary keys = MANDT + SAP key fields
- Every extract query MUST define a Cognite `primary-key` based on the SAP table primary key, **including `MANDT` as the first component**.
- All queries emit a synthetic key: `CONCAT(MANDT, '_', field1, '_', ...)` AS `{TABLE}_PK`.
- Cognite field: `primary-key: "{TABLE}_PK"`.
- Key field choices follow the SAP table reference. Where an older draft (`config-new.yaml`) disagrees with the SAP reference on non-`MANDT` fields, prefer the SAP reference fields used in `config-full.yaml`.

### R2 — Rolling 1-year filter on DATETIMESTAMP
- All extract queries (all 149 tables) MUST filter to the last year of data.
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

### R3 — Query naming, destinations, and categorization
- Query name pattern: `extract-everest-{TABLE}`
- Destination database: `db_databricks_everest_raw`
- Destination table pattern: `tb_{TABLE}`
- Source view pattern: `hub_dev.g_external.v_cognite_{table_lower}_everest`
- Queries MUST be grouped with paired category comments (header + end), matching the style in `config-new.yaml` / `config-full.yaml`.
- Each query SHOULD have a one-line `# TABLE - description` comment.

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
```

---

## Out of scope
- Runtime credential injection (handled outside git)
- Incremental watermarking beyond the rolling 1-year window
- Per-table exceptions to R2 (filter applies to all extracts)
