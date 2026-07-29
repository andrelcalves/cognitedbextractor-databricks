/* MAPPING_MODE_ENABLED: false */
/* {"version":1,"sourceType":"raw","mappings":[{"from":"","to":"key","asType":"STRING"}]} */

/* Do not change */
/* Owner: Andre Alves */

/* Tier 1 - below 1 mi - 51 tables, 5.599.171 expected rows - 49 active */
/* tb_QPGT and tb_MDMA are commented out: the RAW tables do not exist yet, so count(1) fails the run. */
/* MDMA counts 0 rows in Databricks, so the extractor never creates it. Uncomment once each table lands. */
/* expected_rows: Databricks source count from 2026-07-29, recorded in sap/LOAD-STRATEGY.md */
/* Tier 1 loads full and unfiltered (PRD rule R2c), so the whole source table is expected in RAW. */
/* row_ingested below expected_rows points to duplicate primary keys in the source, */
/* since RAW keeps one row per key. Above expected means the source grew since the count. */

with ingested as (
  select 'tb_QMFE'   as `key`, 940696 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_QMFE` union all
  select 'tb_MAST'   as `key`, 781214 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_MAST` union all
  select 'tb_QMUR'   as `key`, 707389 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_QMUR` union all
  select 'tb_SER01'  as `key`, 535535 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_SER01` union all
  select 'tb_QPCT'   as `key`, 474681 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_QPCT` union all
  select 'tb_KNVK'   as `key`, 312248 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_KNVK` union all
  select 'tb_PLKO'   as `key`, 304392 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_PLKO` union all
  select 'tb_MKAL'   as `key`, 281885 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_MKAL` union all
  select 'tb_PLFL'   as `key`, 261515 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_PLFL` union all
  select 'tb_LFA1'   as `key`, 129916 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_LFA1` union all
  select 'tb_PLWP'   as `key`, 117194 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_PLWP` union all
  select 'tb_MPOS'   as `key`, 115255 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_MPOS` union all
  select 'tb_KAPA'   as `key`,  82672 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_KAPA` union all
  select 'tb_CSKT'   as `key`,  82026 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_CSKT` union all
  select 'tb_T156T'  as `key`,  58621 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T156T` union all
  select 'tb_CSKS'   as `key`,  57411 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_CSKS` union all
  select 'tb_SER05'  as `key`,  55035 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_SER05` union all
  select 'tb_MSKU'   as `key`,  49694 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_MSKU` union all
--select 'tb_QPGT'   as `key`,  35263 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_QPGT` union all
  select 'tb_MPLA'   as `key`,  29496 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_MPLA` union all
  select 'tb_CRCO'   as `key`,  28134 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_CRCO` union all
  select 'tb_EQST'   as `key`,  28027 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_EQST` union all
  select 'tb_IFLOTX' as `key`,  21893 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_IFLOTX` union all
  select 'tb_IFLOT'  as `key`,  20068 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_IFLOT` union all
  select 'tb_T023T'  as `key`,  11105 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T023T` union all
  select 'tb_KAZY'   as `key`,  10685 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_KAZY` union all
  select 'tb_T024D'  as `key`,   9285 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T024D` union all
  select 'tb_T006A'  as `key`,   8969 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T006A` union all
  select 'tb_CRTX'   as `key`,   8096 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_CRTX` union all
  select 'tb_TJ30T'  as `key`,   7213 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_TJ30T` union all
  select 'tb_CRHD'   as `key`,   6264 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_CRHD` union all
  select 'tb_KAKO'   as `key`,   5642 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_KAKO` union all
  select 'tb_CRCA'   as `key`,   5638 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_CRCA` union all
  select 'tb_T001L'  as `key`,   4168 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T001L` union all
  select 'tb_QPGR'   as `key`,   3121 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_QPGR` union all
  select 'tb_T024I'  as `key`,   1963 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T024I` union all
  select 'tb_T003P'  as `key`,   1882 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T003P` union all
  select 'tb_T024'   as `key`,   1189 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T024` union all
  select 'tb_T023'   as `key`,    769 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T023` union all
  select 'tb_T134T'  as `key`,    765 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T134T` union all
  select 'tb_T006'   as `key`,    484 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T006` union all
  select 'tb_T001'   as `key`,    430 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T001` union all
  select 'tb_T156'   as `key`,    349 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T156` union all
  select 'tb_T001K'  as `key`,    339 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T001K` union all
  select 'tb_T001W'  as `key`,    336 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T001W` union all
  select 'tb_T024E'  as `key`,     62 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T024E` union all
  select 'tb_TQ80'   as `key`,     60 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_TQ80` union all
  select 'tb_T134'   as `key`,     40 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_T134` union all
  select 'tb_TPST'   as `key`,     39 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_TPST` union all
  select 'tb_TVSB'   as `key`,     18 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_TVSB`
--select 'tb_MDMA'   as `key`,      0 as expected_rows, count(1) as row_ingested from `db_databricks_everest_raw`.`tb_MDMA`
)
select
  `key`,
  expected_rows,
  row_ingested,
  row_ingested - expected_rows as diff,
  case
    when row_ingested = expected_rows then 'OK'
    when row_ingested = 0            then 'EMPTY'
    when row_ingested < expected_rows then 'MISSING'
    else 'EXTRA'
  end as status
from ingested
order by status, expected_rows desc
