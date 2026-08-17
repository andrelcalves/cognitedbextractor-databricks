# Estratégia de carga por volume — SAP Everest

Documento de apoio para as 149 views nos três extractors contínuos. Volumes medidos em 29/07/2026; regras de extract em [`PRD.md`](PRD.md).

**Status atual:** incremental desde `2026-08-01`, schedule `15m`, destino `db_databricks_glb_raw.tb_*Everest`, três configs (`config-extract-1|2|3.yaml`), `parallelism: 1` com ordem small→large.

---

## Contexto

- Fonte dos volumes: contagens do time de Databricks em 29/07/2026 (128 views em `g_external`).
- **126** dessas estão nos nossos configs. VBUK / VBUP existem no schema mas **não** são extraídas.
- **23** views nos configs **sem contagem** (seção abaixo).
- Total medido nas 126: **~16,3 bilhões** de linhas.
- Histórico até julho já está no Cognite. Estes extractors fazem catch-up **agosto → hoje** na primeira run e depois só delta a cada 15 minutos. Não há migração de `db_databricks_everest_raw` → `db_databricks_glb_raw` nesta entrega.

---

## Resumo por tier

| Tier | Faixa | Views | Linhas | % | Estratégia nesta entrega |
|------|-------|-------|--------|---|--------------------------|
| 5 | > 1 bi | 3 | 10.064.072.150 | 61,9% | Incremental 15m; 1 tabela por extractor; última na fila |
| 4 | 100 mi–1 bi | 12 | 5.258.346.885 | 32,3% | Incremental 15m; split 4/4/4 |
| 3 | 10–100 mi | 27 | 831.272.603 | 5,1% | Incremental 15m; split 9/9/9 |
| 2 | 1–10 mi | 33 | 104.032.536 | 0,64% | Incremental 15m (não mais full 1 ano) |
| 1 | < 1 mi | 51 | 5.599.171 | 0,03% | Incremental 15m (não mais full sem filtro) |
| ? | sem contagem | 23 | — | — | Incremental 15m; split ~7–8 |
| | **Total medido** | **126** | **16.263.323.345** | **100%** | |

Três views (Tier 5) concentram ~62% do volume; as 15 maiores ~94%.

---

## Três extractors (split por tier)

Algoritmo: para cada tier `5→4→3→2→1→?`, ordenar por linhas desc; atribuir ao extractor com menos tabelas **naquele tier**, depois menor soma de linhas.

Ordem **dentro** de cada YAML: Tier 1 → `?` → 2 → 3 → 4 → 5, tamanho ascendente. Com `parallelism: 1`, as pequenas terminam antes das grandes **naquele processo**.

| Extractor | Arquivo | ~N | Tier 5 | Pipeline | State table |
|-----------|---------|----|--------|----------|-------------|
| 1 | `config-extract-1.yaml` | 49 | MDTB | `ep_databricks_everest_dbExtractor_1` | `everest_extract_1` |
| 2 | `config-extract-2.yaml` | 50 | COEP | `ep_databricks_everest_dbExtractor_2` | `everest_extract_2` |
| 3 | `config-extract-3.yaml` | 50 | EBAN | `ep_databricks_everest_dbExtractor_3` | `everest_extract_3` |

### Extractor 1

- **T5:** MDTB
- **T4:** COSP, AUFM, VBPA, VBEP
- **T3:** AFVC, KSSK, VBBE, LIKP, VTTK, AUFK, MCHB, PMCO, QAVE
- **T2:** OBJK, EKKO, MAPL, EQUZ, MARC, QMEL, MAKT, KNVV, MARM, MARA, PLMZ
- **T1:** QMUR, KNVK, PLFL, MPOS, T156T, MSKU, CRCO, IFLOT, T024D, TJ30T, CRCA, T024I, T023, T001, T001W, T134, MDMA
- **T?:** KAKT, IMPTT, EAPL, T430T, RKPF, T353I, T370F

### Extractor 2

- **T5:** COEP
- **T4:** RESB, MSEG, MKPF, AFRU
- **T3:** AFVV, KEKO, EKBE, STAS, MARD, STPO, VBAK, AFKO, ILOA
- **T2:** MCH1, AFPO, MHIO, PLAS, MDKP, EINE, QMAT, SER03, KNA1, STKO, AFFW
- **T1:** MAST, QPCT, MKAL, PLWP, CSKT, SER05, MPLA, IFLOTX, KAZY, CRTX, KAKO, QPGR, T024, T006, T001K, TQ80, TVSB
- **T?:** TC25T, IFLOS, AFRC, T430, T301, T156HT, T357, T370T

### Extractor 3

- **T5:** EBAN
- **T4:** JEST, VBFA, AUSP, CKIS
- **T3:** LIPS, VBAP, QAMR, AFWI, VTTP, EKET, EKPO, MCHA, QALS
- **T2:** LQUA, AFIH, EKKN, QMSM, PLPO, MBEW, QMIH, MHIS, EQUI, EQKT, EINA
- **T1:** QMFE, SER01, PLKO, LFA1, KAPA, CSKS, QPGT, EQST, T023T, T006A, CRHD, T001L, T003P, T134T, T156, T024E, TPST
- **T?:** TC25, TC37A, IMRG, TAPL, LAGP, TVEPZ, T356, T370S

### Ops — catch-up vs 15m

Na primeira execução, Tier 4/5 a partir de agosto podem levar horas/dias. O intervalo de 15m só se estabiliza depois que o watermark de cada query alcança o presente. Depois disso o delta costuma caber em 15m; Tier 5 ainda merece monitoramento. Opcional depois: `1h` só no Tier 5 se houver overlap.

---

## Tier 5 — acima de 1 bilhão

| View | Linhas |
|------|--------|
| MDTB | 5.379.495.282 |
| COEP | 2.949.490.993 |
| EBAN | 1.735.085.875 |

**Estratégia aplicada:** incremental com state store; `initial-start` 2026-08-01; uma tabela por extractor; última na fila serial.

Ainda futuro: projeção de colunas; questionar se MDTB (MRP) deve ser replicada integralmente.

---

## Tier 4 — 100 milhões a 1 bilhão

| View | Linhas |
|------|--------|
| JEST | 848.522.773 |
| RESB | 843.543.737 |
| COSP | 706.716.382 |
| VBFA | 677.404.018 |
| MSEG | 490.375.041 |
| AUFM | 350.809.194 |
| AUSP | 327.379.157 |
| MKPF | 292.610.764 |
| VBPA | 263.924.069 |
| CKIS | 200.518.975 |
| AFRU | 144.430.339 |
| VBEP | 112.112.436 |

**Estratégia aplicada:** incremental 15m; split 4/4/4. Projeção de colunas ainda futura.

---

## Tier 3 — 10 a 100 milhões

| View | Linhas | | View | Linhas |
|------|--------|-|------|--------|
| LIPS | 85.426.484 | | VTTK | 17.116.336 |
| AFVV | 72.707.752 | | EKET | 16.500.379 |
| AFVC | 72.700.785 | | STPO | 15.348.670 |
| VBAP | 70.287.949 | | AUFK | 14.446.636 |
| KEKO | 58.881.095 | | EKPO | 14.321.553 |
| KSSK | 46.527.246 | | VBAK | 14.299.699 |
| QAMR | 46.452.589 | | MCHB | 13.975.831 |
| EKBE | 43.813.684 | | MCHA | 13.083.956 |
| VBBE | 33.057.061 | | AFKO | 12.749.465 |
| AFWI | 31.390.016 | | PMCO | 12.346.036 |
| STAS | 27.362.006 | | QALS | 11.274.432 |
| LIKP | 25.404.608 | | ILOA | 11.207.563 |
| VTTP | 22.340.431 | | QAVE | 11.083.415 |
| MARD | 17.166.926 | | | |

**Estratégia aplicada:** incremental 15m; split 9/9/9.

---

## Tier 2 — 1 a 10 milhões

| View | Linhas | | View | Linhas |
|------|--------|-|------|--------|
| LQUA | 9.567.436 | | QMAT | 1.954.680 |
| MCH1 | 9.263.775 | | MAKT | 1.707.991 |
| OBJK | 8.800.815 | | MHIS | 1.622.661 |
| AFIH | 6.415.666 | | SER03 | 1.543.771 |
| AFPO | 6.335.594 | | KNVV | 1.487.203 |
| EKKO | 5.377.290 | | EQUI | 1.434.080 |
| EKKN | 5.257.736 | | KNA1 | 1.408.544 |
| MHIO | 5.186.856 | | MARM | 1.399.952 |
| MAPL | 3.396.230 | | EQKT | 1.397.294 |
| QMSM | 3.158.949 | | STKO | 1.359.773 |
| PLAS | 2.767.802 | | MARA | 1.254.124 |
| EQUZ | 2.766.450 | | EINA | 1.128.342 |
| PLPO | 2.552.329 | | AFFW | 1.125.556 |
| MDKP | 2.502.142 | | PLMZ | 1.106.480 |
| MARC | 2.331.034 | | | |
| MBEW | 2.282.427 | | | |
| EINE | 2.159.624 | | | |
| QMEL | 1.989.965 | | | |
| QMIH | 1.989.965 | | | |

**Estratégia aplicada:** incremental desde 2026-08-01 (substitui a carga full com janela de 1 ano).

---

## Tier 1 — abaixo de 1 milhão

| View | Linhas | | View | Linhas | | View | Linhas |
|------|--------|-|------|--------|-|------|--------|
| QMFE | 940.696 | | IFLOTX | 21.893 | | T023 | 769 |
| MAST | 781.214 | | IFLOT | 20.068 | | T134T | 765 |
| QMUR | 707.389 | | T023T | 11.105 | | T006 | 484 |
| SER01 | 535.535 | | KAZY | 10.685 | | T001 | 430 |
| QPCT | 474.681 | | T024D | 9.285 | | T156 | 349 |
| KNVK | 312.248 | | T006A | 8.969 | | T001K | 339 |
| PLKO | 304.392 | | CRTX | 8.096 | | T001W | 336 |
| MKAL | 281.885 | | TJ30T | 7.213 | | T024E | 62 |
| PLFL | 261.515 | | CRHD | 6.264 | | TQ80 | 60 |
| LFA1 | 129.916 | | KAKO | 5.642 | | T134 | 40 |
| PLWP | 117.194 | | CRCA | 5.638 | | TPST | 39 |
| MPOS | 115.255 | | T001L | 4.168 | | TVSB | 18 |
| KAPA | 82.672 | | QPGR | 3.121 | | MDMA | 0 |
| CSKT | 82.026 | | T024I | 1.963 | | | |
| T156T | 58.621 | | T003P | 1.882 | | | |
| CSKS | 57.411 | | T024 | 1.189 | | | |
| SER05 | 55.035 | | | | | | |
| MSKU | 49.694 | | | | | | |
| QPGT | 35.263 | | | | | | |
| MPLA | 29.496 | | | | | | |
| CRCO | 28.134 | | | | | | |
| EQST | 28.027 | | | | | | |

**Estratégia aplicada:** incremental desde 2026-08-01 (substitui full unfiltered). Cadastros que quase não mudam ainda entram na fila small-first; o delta pós-catch-up tende a ser mínimo.

---

## Views sem contagem

23 views nos configs sem medição na planilha. Tratadas como Tier `?`, carregadas após o Tier 1 e antes do Tier 2 em cada extractor.

TC25, TC25T, KAKT, TC37A, IFLOS, IMPTT, IMRG, AFRC, EAPL, TAPL, T430, T430T, LAGP, T301, RKPF, TVEPZ, T156HT, T353I, T356, T357, T370F, T370S, T370T.

---

## Destino RAW e chaves

- Database: `db_databricks_glb_raw`
- Tabela: `tb_{Pascal}Everest` (ex. `tb_AffwEverest`)
- `primary-key: "{key}"` — GUID opaco (SHA-256 hex) gerado no SQL: `sha2(concat_ws('_', <colunas de negócio>), 256) AS key`
- Não usar `primary-key: "{MANDT}_..."` nem `uuid()` aleatório
- Detalhes: [`PRD.md`](PRD.md) R1–R6

---

## Estado da implementação

Aplicado:

- Tags de tier nas 149 queries
- Três configs contínuos com split por tier
- Incremental `DATETIMESTAMP` / `initial-start: 2026-08-01` / schedule `15m`
- Destino `db_databricks_glb_raw` / `tb_*Everest`
- State store RAW por extractor; `parallelism: 1`; ordem small→large

Fica para depois:

- Criar os 3 Extraction Pipelines no CDF UI
- Projeção de colunas nos Tiers 4/5
- Confirmar clustering/partition em `DATETIMESTAMP` no Databricks
- Intervalo maior só no Tier 5 se necessário após o catch-up
