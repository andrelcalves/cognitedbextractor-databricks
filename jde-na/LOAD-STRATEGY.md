# Estratégia de carga — JDE NA

Documento de apoio para as **30** views no extractor contínuo único. Regras em [`PRD.md`](PRD.md).

**Status:** um extractor (`config.yaml`); incremental desde `2026-08-01`; schedule `15m`; destino `db_databricks_glb_raw.tb_*Jdena`; `parallelism: 1`; `key` = SHA-256 full-row JSON.

---

## Contexto

- Fonte: `hub_dev.g_external.v_cognite_F*_jdena`.
- Catch-up agosto→hoje na primeira run; depois delta a cada 15 minutos.
- Sem contagens oficiais NA no repo; a ordem small→large usa *size hints* das volumes JDE IN (mesmos IDs) só para sequenciar a fila.
- Views que já falharam com `TABLE_OR_VIEW_NOT_FOUND` (F4108, F42019, F4801) permanecem no config — falham rápido e a fila serial segue.

---

## Extractor único

| Item | Valor |
|------|--------|
| Arquivo | `config.yaml` |
| Pipeline | `ep_databricks_jde_na_dbExtractor` |
| State | `db_extractor_state` / `jdena_extract` |
| Queries | 30 |

Ordem (small → large, fim da fila = mais pesadas):

F30026, F0010, F3003, F40072, F41002, F4201, F3002, F0006, F0101, F0911, F30008, F3111, F3112, F3411, F4101, F41021, F4105, F4108, F42019, F43092, F4311, F43121, F43199, F4801, F4211, F4102, F03012, F4301, **F42119**, **F4111**

### Ops — catch-up vs 15m
F42119 e F4111 dominam o tempo na primeira carga. O intervalo de 15m só estabiliza depois do watermark nessas queries.

---

## Destino RAW e chaves

- Database: `db_databricks_glb_raw`
- Tabela: `tb_{TABLE}Jdena` (ex. `tb_F42119Jdena`)
- `primary-key: "{key}"` — SHA-256 of the full row as canonical sorted JSON (`struct(*)`)
- Qualquer mudança de coluna gera nova chave RAW (append)

---

## Estado da implementação

Aplicado:

- Um config contínuo com as 30 queries
- Incremental, `key`, `db_databricks_glb_raw`, ordem small→large
- Monolito antigo em `config.yaml.bak`

Fica para depois:

- Contagens reais NA e reordenar se necessário
- Remover/comentar views inexistentes se o time confirmar
