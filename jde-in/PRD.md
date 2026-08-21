# Product Requirements Document (PRD) — JDE IN

## Product
Databricks → Cognite JDE IN DB Extractor (single continuous instance)

## Purpose
Extract JDE India views from Databricks (`hub_dev.g_external.v_cognite_f*_jdeint`) into Cognite RAW (`db_databricks_glb_raw.tb_*Jdeint`) with full-row SHA-256 keys (`primary-key: "{key}"`), incremental load from **2026-08-01**, and a **15-minute** continuous schedule.

**Prerequisite:** data through July is already in Cognite. This config does **not** re-extract pre-August history.

## File layout
| File | Role |
|------|------|
| `jde-in/config.yaml` | Single continuous extractor (15 queries) |
| `jde-in/config.yaml.bak` | Previous config (reference) |
| `jde-in/PRD.md` | This document |
| `jde-in/LOAD-STRATEGY.md` | Ops / ordering notes |

| Concern | Value |
|---------|--------|
| Extraction pipeline | `ep_databricks_jde_in_dbExtractor` |
| RAW database | `db_databricks_glb_raw` |
| RAW table pattern | `tb_{Table}Jdeint` (e.g. `tb_F3003Jdeint`) |
| State store | RAW `db_extractor_state` / `jdein_extract` |
| Mode | `continuous` |
| Parallelism | `1` (small before large) |
| Schedule | `interval` / `15m` |
| Incremental field | `DATETIMESTAMP` |
| Initial start | `2026-08-01 00:00:00` |

Legacy `db_databricks_jde_in_raw.tb_*` is out of scope (no migration in this change).

---

## Rules

### R1 — Cognite `primary-key` = full-row hash (`key`)
- Every RAW query MUST set `primary-key: "{key}"`.
- Compute `key` in SQL as SHA-256 of a canonical JSON encoding of the **full row** (`struct(*)`): nulls kept, map entries sorted by key, `& < >` and Unicode line separators escaped to `\\u0026` / `\\u003c` / `\\u003e` / `\\u2028` / `\\u2029`.
- Do **not** use `uuid()`, business-key `concat_ws`, or column templates as `primary-key`.
- Full-row hash means any column change yields a **new** RAW key (append).

```sql
SELECT
  sha2(
    replace(replace(replace(replace(replace(
      to_json(
        map_from_entries(
          array_sort(
            map_entries(
              from_json(
                to_json(struct(*), map('ignoreNullFields', 'false')),
                'MAP<STRING, VARIANT>'
              )
            ),
            (l, r) -> CASE
              WHEN l.key < r.key THEN -1
              WHEN l.key > r.key THEN 1
              ELSE 0
            END
          )
        ),
        map('ignoreNullFields', 'false')
      ),
      '&', '\\u0026'),
      '<', '\\u003c'),
      '>', '\\u003e'),
      chr(8232), '\\u2028'),
      chr(8233), '\\u2029'
    ),
    256
  ) AS key,
  *
```

### R2 — Incremental watermark (all 15 queries)
- `incremental-field: DATETIMESTAMP`
- `initial-start: "2026-08-01 00:00:00"`
- SQL: `WHERE {incremental_field} >= '{start_at}' ORDER BY {incremental_field} ASC`
- No rolling 1-year window; no `LIMIT 10`

### R3 — Naming
- Query: `extract-jdein-{TABLE}`
- Source: `hub_dev.g_external.v_cognite_{table_lower}_jdeint`
- Destination DB: `db_databricks_glb_raw`
- Destination table: `tb_{TABLE}Jdeint`

### R4 — Single extractor, small-first
- One config / one pipeline / one state table
- Queries ordered ascending by measured IN volumes (2026-08-03 log); heavy last: F42119, F4111
- `parallelism: 1`

### R5 — Continuous schedule
- `extractor.mode: continuous`
- Every query `schedule: { type: interval, expression: 15m }`

### R6 — Secrets in git
- Blank Host, HTTPPath, tenant, client-id, secret, PWD in committed YAML

---

## Example query shape

See R1 for the `key` SELECT; destination example:

```yaml
    destination:
      type: "raw"
      database: "db_databricks_glb_raw"
      table: "tb_F3003Jdeint"
    primary-key: "{key}"
```

---

## Out of scope
- Splitting into multiple JDE IN extractors
- JDE NA / SAP Everest (separate folders)
- Migrating `db_databricks_jde_in_raw` → `db_databricks_glb_raw`
