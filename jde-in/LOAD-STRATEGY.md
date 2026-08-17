# Estratégia de carga — JDE IN

Documento de apoio para as **15** views no extractor contínuo único. Regras em [`PRD.md`](PRD.md).

**Status:** um extractor (`config.yaml`); incremental desde `2026-08-01`; schedule `15m`; destino `db_databricks_glb_raw.tb_*Jdein`; `parallelism: 1`; `key` = SHA-256 full-row JSON.

---

## Contexto

- Fonte: `hub_dev.g_external.v_cognite_f*_jdeint` (suffixo de view `jdeint`, lowercase).
- Catch-up agosto→hoje na primeira run; depois delta a cada 15 minutos.
- Volumes de ordenação: log `dbextractor.log.2026-08-03` (F4111 / F42119 ainda incompletos naquele corte, mas dominam o volume).
- `F4108` já falhou com `TABLE_OR_VIEW_NOT_FOUND`; permanece no config e falha rápido na fila serial.
- `F40306` OK com 0 linhas na janela anterior.

---

## Extractor único

| Item | Valor |
|------|--------|
| Arquivo | `config.yaml` |
| Pipeline | `ep_databricks_jde_in_dbExtractor` |
| State | `db_extractor_state` / `jdein_extract` |
| Queries | 15 |

Ordem (small → large):

F40306, F30026, F0010, F3003, F40072, F41002, F4201, F3002, F4108, F4211, F4102, F03012, F4301, **F42119**, **F4111**

### Ops — catch-up vs 15m
F42119 e F4111 dominam o tempo. O intervalo de 15m só estabiliza depois do watermark nessas queries.

---

## Destino RAW e chaves

- Database: `db_databricks_glb_raw`
- Tabela: `tb_{TABLE}Jdein` (ex. `tb_F3003Jdein`)
- `primary-key: "{key}"` — SHA-256 of the full row as canonical sorted JSON (`struct(*)`)
- Qualquer mudança de coluna gera nova chave RAW (append)

---

## Estado da implementação

Aplicado:

- Um config contínuo com as 15 queries
- Incremental, `key`, `db_databricks_glb_raw`, ordem small→large
- Config anterior em `config.yaml.bak`

Fica para depois:

- Comentar/remover `F4108` se a view não for criada
