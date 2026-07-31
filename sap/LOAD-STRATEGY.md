# Estratégia de carga por volume — SAP Everest

Documento de apoio para definir como extrair as 149 views configuradas em `sap/config.yaml`. Classifica cada view em um tier de volume e propõe a forma de carga correspondente.

Status: proposta para discussão. Nenhuma alteração foi aplicada em `sap/config.yaml`.

---

## Contexto

- Fonte dos volumes: contagens fornecidas pelo time de Databricks em 29/07/2026, cobrindo 128 views do schema `g_external`.
- Dessas 128, **126 estão no nosso `config.yaml`**. As duas restantes (VBUK com 67.672.474 linhas e VBUP com 137.737.173) existem no schema mas não são extraídas hoje.
- As **23 views restantes do `config.yaml` não têm contagem** e estão detalhadas na seção "Views sem contagem".
- Total das 126 views configuradas e medidas: **16.263.323.345 linhas**, aproximadamente 16,3 bilhões.

### O que roda hoje

Na execução de 27/07/2026 o extractor leu 43 das 149 views; as outras 106 falharam com `TABLE_OR_VIEW_NOT_FOUND`. Das 43 que funcionam, 20 têm contagem e somam **4.897.404.079 linhas**, sendo que só a COEP responde por 60% desse subtotal.

As 20 são: COEP, COSP, AUFM, AUSP, CKIS, AFRU, AFVV, AFVC, AFWI, AUFK, AFKO, AFIH, AFPO, AFFW, CSKT, CSKS, CRCO, CRTX, CRHD e CRCA.

Em outras palavras, a lentidão atual vem de cinco views — COEP, COSP, AUFM, AUSP e CKIS — que sozinhas somam 4,5 bilhões de linhas. Quando as 106 bloqueadas forem liberadas, o volume salta de 4,9 para 16,3 bilhões, mais de três vezes. A estratégia precisa estar definida antes disso.

---

## Resumo por tier

| Tier | Faixa | Views | Linhas | % do total | Estratégia proposta |
|---|---|---|---|---|---|
| 5 | Acima de 1 bilhão | 3 | 10.064.072.150 | 61,9% | Incremental + backfill mensal + colunas selecionadas |
| 4 | 100 mi a 1 bi | 12 | 5.258.346.885 | 32,3% | Incremental + backfill em janelas |
| 3 | 10 mi a 100 mi | 27 | 831.272.603 | 5,1% | Incremental + backfill em passada única |
| 2 | 1 mi a 10 mi | 33 | 104.032.536 | 0,64% | Carga full com janela de 1 ano |
| 1 | Abaixo de 1 milhão | 51 | 5.599.171 | 0,03% | Carga full sem filtro |
| | **Total** | **126** | **16.263.323.345** | **100%** | |

A concentração é o dado mais importante deste levantamento. **Três views representam 62% de todo o volume, e as 15 maiores representam 94%.** No outro extremo, 51 views — 40% do total de tabelas — somam 5,6 milhões de linhas juntas, ou 0,03% do total. Esforço gasto otimizando o Tier 1 é esforço desperdiçado; o ganho está inteiramente nos Tiers 4 e 5.

---

## Tier 5 — acima de 1 bilhão de linhas

| View | Linhas |
|---|---|
| MDTB | 5.379.495.282 |
| COEP | 2.949.490.993 |
| EBAN | 1.735.085.875 |

**Estratégia.** Carga full está descartada. Estas três precisam de:

1. **Incremental obrigatório** com state store, usando `DATETIMESTAMP` como `incremental-field`.
2. **Backfill em janelas mensais**, nunca em passada única. Uma query de 5 bilhões de linhas tem alta chance de estourar timeout do cluster ou da conexão ODBC, e um retry recomeça do zero.
3. **Projeção explícita de colunas** no lugar de `SELECT *`. COEP em particular é uma tabela muito larga; ler apenas as colunas necessárias reduz a I/O de forma proporcional e costuma render mais que o filtro de linhas.
4. **Decisão explícita sobre profundidade de histórico.** Precisamos definir se o caso de uso exige histórico completo ou se a janela de 1 ano basta. Sem isso, o backfill não tem escopo definido.

Ponto específico da MDTB: é a lista de necessidades do MRP, regerada continuamente pelo próprio SAP. Vale questionar se faz sentido replicar essa view integralmente, ou se o consumo deveria ser sob demanda direto no Databricks.

---

## Tier 4 — de 100 milhões a 1 bilhão

| View | Linhas |
|---|---|
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

**Estratégia.** Incremental obrigatório com state store. O backfill inicial deve ser feito em janelas — trimestrais ou anuais conforme o volume de cada uma — e só depois a query passa a operar em modo delta.

Nas mais largas do grupo (COSP, CKIS, MSEG, MKPF) vale aplicar também a projeção explícita de colunas.

Cinco destas — COSP, AUFM, AUSP, CKIS e AFRU — já estão em produção hoje e são a causa direta da lentidão atual. São as candidatas naturais para o piloto do incremental, porque o ganho é mensurável imediatamente e não depende do desbloqueio das 106.

---

## Tier 3 — de 10 a 100 milhões

| View | Linhas | | View | Linhas |
|---|---|---|---|---|
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

**Estratégia.** Incremental com state store, mas o backfill inicial cabe em passada única — nesta faixa não é necessário fatiar em janelas.

---

## Tier 2 — de 1 a 10 milhões

| View | Linhas | | View | Linhas |
|---|---|---|---|---|
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

**Estratégia.** Carga full mantendo a janela de 1 ano, com o filtro reescrito para ser sargável (ver seção de validação). O grupo inteiro soma 104 milhões de linhas, menos de 1% do total, então o custo de recarregar tudo é aceitável e não justifica a complexidade do state store.

Migrar para incremental aqui é opcional e só se justifica se o tempo total de janela ainda incomodar depois que os Tiers 4 e 5 estiverem resolvidos.

> **Status: aplicado.** As 33 queries do Tier 2 usam o filtro sargável `DATETIMESTAMP >= date_format(current_timestamp() - INTERVAL 1 YEAR, 'yyyy-MM-dd HH:mm:ss')` e não têm `LIMIT 10`.

---

## Tier 1 — abaixo de 1 milhão

| View | Linhas | | View | Linhas | | View | Linhas |
|---|---|---|---|---|---|---|---|
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

**Estratégia.** Carga full **sem filtro de data**. São predominantemente tabelas de customizing e cadastro, e o grupo inteiro soma 5,6 milhões de linhas — recarregar tudo é mais barato que qualquer lógica incremental.

O motivo de tirar o filtro, e não só o `LIMIT`, é que tabelas de customizing raramente sofrem alteração, então uma janela de 1 ano sobre `DATETIMESTAMP` pode retornar zero linhas mesmo com a tabela populada. Isso já aconteceu na execução de 27/07: a **IFLOS retornou 0 linhas** apesar de a view existir e ser legível. Manter o filtro no Tier 1 geraria tabelas vazias no CDF RAW sem nenhum erro aparente, o que é pior que não filtrar.

> **Status: aplicado.** As 51 queries do Tier 1 já estão em `config.yaml` sem `WHERE` e sem `LIMIT 10`. Regra registrada como R2c no `PRD.md`.

---

## Views sem contagem

Estas 23 views estão no `config.yaml` mas não aparecem na planilha de volumes. Não é coincidência: são exatamente as mesmas 23 que o extractor **consegue ler** em `hub_dev.g_external` mas que **não constam** no inventário de 164 objetos compartilhado pelo time de Databricks. É a mesma divergência de escopo já levantada — o inventário e a nossa conexão parecem apontar para catálogos diferentes.

A coluna "linhas lidas em 27/07" vem do teste de disponibilidade, que usava `LIMIT 10`. Portanto o valor 10 significa apenas "pelo menos 10" e não diz nada sobre o tamanho real.

| View | Descrição | Linhas lidas em 27/07 | Risco de volume |
|---|---|---|---|
| IMRG | Documentos de medição | 10 (limitado) | **Alto** |
| RKPF | Cabeçalho de reserva | 10 (limitado) | **Alto** |
| LAGP | Posições de depósito | 10 (limitado) | **Alto** |
| IMPTT | Pontos de medição | 10 (limitado) | Médio |
| AFRC | Confirmações incorretas | 10 (limitado) | Médio |
| TAPL | Roteiros por local de instalação | 10 (limitado) | Médio |
| EAPL | Roteiros por equipamento | 10 (limitado) | Médio |
| TC25 | Fórmulas de centro de trabalho | 10 (limitado) | Baixo |
| TC25T | Textos das fórmulas | 10 (limitado) | Baixo |
| TC37A | Definição de turnos | 10 (limitado) | Baixo |
| KAKT | Descrição de capacidade | 10 (limitado) | Baixo |
| T301 | Tipos de depósito WM | 10 (limitado) | Baixo |
| T430 | Chaves de controle | 10 (limitado) | Baixo |
| T430T | Textos das chaves de controle | 10 (limitado) | Baixo |
| T156HT | Texto principal do tipo de movimento | 10 (limitado) | Baixo |
| T353I | Tipos de atividade de manutenção | 10 (limitado) | Baixo |
| T356 | Prioridades | 10 (limitado) | Baixo |
| T357 | Setores da fábrica | 10 (limitado) | Baixo |
| TVEPZ | Determinação de categoria de item | 10 (limitado) | Baixo |
| T370F | Categoria de local de instalação | 3 | Baixo |
| T370T | Categorias de equipamento | 5 | Baixo |
| T370S | Indicadores de estrutura | 1 | Baixo |
| IFLOS | Denominações alternativas de local | 0 | Baixo |

As três marcadas como risco alto são as que podem mudar a classificação. A **IMRG** registra uma linha por leitura de contador e, em plantas com medição automatizada, chega facilmente à casa das centenas de milhões. A **RKPF** é o cabeçalho das reservas cujos itens (RESB) já contam 843 milhões, então dezenas de milhões é um cenário plausível. A **LAGP** costuma ficar na casa dos milhões.

O `IFLOS` retornou zero porque o filtro de 1 ano não encontrou registros na janela, não porque a view esteja vazia — motivo pelo qual a contagem abaixo deve ser feita sem filtro.

### SQL para medir

```sql
SELECT 'IMRG'   AS sap_table, count(*) AS row_count FROM hub_dev.g_external.v_cognite_imrg_everest
UNION ALL SELECT 'RKPF',   count(*) FROM hub_dev.g_external.v_cognite_rkpf_everest
UNION ALL SELECT 'LAGP',   count(*) FROM hub_dev.g_external.v_cognite_lagp_everest
UNION ALL SELECT 'IMPTT',  count(*) FROM hub_dev.g_external.v_cognite_imptt_everest
UNION ALL SELECT 'AFRC',   count(*) FROM hub_dev.g_external.v_cognite_afrc_everest
UNION ALL SELECT 'TAPL',   count(*) FROM hub_dev.g_external.v_cognite_tapl_everest
UNION ALL SELECT 'EAPL',   count(*) FROM hub_dev.g_external.v_cognite_eapl_everest
UNION ALL SELECT 'TC25',   count(*) FROM hub_dev.g_external.v_cognite_tc25_everest
UNION ALL SELECT 'TC25T',  count(*) FROM hub_dev.g_external.v_cognite_tc25t_everest
UNION ALL SELECT 'TC37A',  count(*) FROM hub_dev.g_external.v_cognite_tc37a_everest
UNION ALL SELECT 'KAKT',   count(*) FROM hub_dev.g_external.v_cognite_kakt_everest
UNION ALL SELECT 'T301',   count(*) FROM hub_dev.g_external.v_cognite_t301_everest
UNION ALL SELECT 'T430',   count(*) FROM hub_dev.g_external.v_cognite_t430_everest
UNION ALL SELECT 'T430T',  count(*) FROM hub_dev.g_external.v_cognite_t430t_everest
UNION ALL SELECT 'T156HT', count(*) FROM hub_dev.g_external.v_cognite_t156ht_everest
UNION ALL SELECT 'T353I',  count(*) FROM hub_dev.g_external.v_cognite_t353i_everest
UNION ALL SELECT 'T356',   count(*) FROM hub_dev.g_external.v_cognite_t356_everest
UNION ALL SELECT 'T357',   count(*) FROM hub_dev.g_external.v_cognite_t357_everest
UNION ALL SELECT 'TVEPZ',  count(*) FROM hub_dev.g_external.v_cognite_tvepz_everest
UNION ALL SELECT 'T370F',  count(*) FROM hub_dev.g_external.v_cognite_t370f_everest
UNION ALL SELECT 'T370S',  count(*) FROM hub_dev.g_external.v_cognite_t370s_everest
UNION ALL SELECT 'T370T',  count(*) FROM hub_dev.g_external.v_cognite_t370t_everest
UNION ALL SELECT 'IFLOS',  count(*) FROM hub_dev.g_external.v_cognite_iflos_everest
ORDER BY row_count DESC;
```

---

## Pontos a validar antes de fechar a estratégia

### 1. As views são feed de mudanças (CDC) ou retrato do estado atual?

Esta é a pergunta mais importante da lista, porque muda a semântica de tudo o que vem depois.

EBAN com 1,74 bilhão e MDTB com 5,38 bilhões são números incompatíveis com o estado atual dessas tabelas no SAP — requisições de compra e listas de MRP não atingem essa ordem de grandeza como registros vigentes. A presença da própria coluna `DATETIMESTAMP` em todas as views reforça a hipótese de que cada linha é uma versão de registro, e não um registro atual.

Se a hipótese se confirmar, duas consequências:

- **Carga full deixa de ser apenas cara e passa a ser semanticamente errada**, porque estaríamos trazendo o histórico completo de alterações para dentro do CDF RAW.
- **A chave primária que usamos hoje passa a colidir.** Todas as queries montam `CONCAT(MANDT, campos-chave)` como chave da linha no RAW. Se existem N versões do mesmo registro, as N linhas disputam a mesma chave e o RAW guarda a última que for gravada. Como as queries não têm `ORDER BY`, a ordem de gravação não é determinística e a versão que sobrevive é arbitrária, não necessariamente a mais recente. Nesse cenário seria preciso ou deduplicar na origem, ou incluir `DATETIMESTAMP` na chave, ou ordenar a query por `DATETIMESTAMP` ascendente.

Vale confirmar também se `DATETIMESTAMP` reflete o momento da alteração no SAP ou o momento da replicação para o Databricks. Isso determina se a carga incremental pode perder registros que chegam atrasados.

### 2. MDMA aparece com zero linhas

A view consta na planilha com contagem zero. Precisa ser confirmado se a tabela está realmente vazia na origem ou se houve falha na construção da view. Enquanto não estiver esclarecido, ela fica classificada no Tier 1 por convenção, não por medição.

### 3. O filtro atual impede o pruning de arquivos

Todas as 149 queries usam hoje:

```sql
WHERE coalesce(
        try_to_timestamp(DATETIMESTAMP, 'yyyy-MM-dd HH:mm:ss.SSSSSS'),
        try_to_timestamp(DATETIMESTAMP, 'yyyy-MM-dd HH:mm:ss')
      ) >= current_timestamp() - INTERVAL 1 YEAR
```

Envolver a coluna em uma função torna o predicado não-sargável: o Databricks não consegue usar as estatísticas de mínimo e máximo dos arquivos para descartar blocos, e acaba lendo e convertendo a coluna inteira de cada tabela mesmo quando só uma fração das linhas está na janela. Em COEP isso significa converter 2,9 bilhões de valores para depois descartar a maioria.

Como `DATETIMESTAMP` é string no formato `yyyy-MM-dd HH:mm:ss[.SSSSSS]`, que é zero-padded, a comparação lexicográfica produz exatamente a mesma ordenação da comparação por timestamp. Isso vale inclusive entre os dois formatos presentes, porque `'2024-11-18 17:57:50'` é lexicograficamente menor que `'2024-11-18 17:57:50.000001'`. A reescrita abaixo preserva a semântica e permite o pruning:

```sql
WHERE DATETIMESTAMP >= date_format(current_timestamp() - INTERVAL 1 YEAR, 'yyyy-MM-dd HH:mm:ss')
```

Esta mudança vale para todos os tiers e independe da decisão sobre carga incremental. É também o que viabiliza o incremental de forma limpa, já que o `{start_at}` do state store seria comparado como string na mesma ordem.

O ganho real depende de as tabelas de origem estarem particionadas, com Z-order ou liquid clustering sobre `DATETIMESTAMP`. Vale confirmar isso com o time de Databricks: sem nenhum tipo de clustering, o pruning não acontece nem com o predicado sargável.

### 4. O `SELECT *` anula o column pruning

Todas as queries seguem o padrão `SELECT CONCAT(...) AS TABELA_PK, *`. Em formato colunar, ler todas as colunas quando só um subconjunto é necessário desperdiça I/O de forma proporcional à largura da tabela. Em tabelas como COEP, COSP, MSEG e MARC, que têm centenas de colunas, restringir a projeção às colunas efetivamente consumidas costuma render mais que qualquer filtro de linhas.

Isso exige um levantamento de quais colunas cada consumidor usa de fato, o que é um trabalho à parte — mas para as 15 views dos Tiers 4 e 5, que concentram 94% do volume, o retorno justifica o esforço.

### 5. Volume total no CDF RAW

Os 16,3 bilhões de linhas são o volume da origem, não necessariamente o que deve chegar ao CDF RAW. Vale dimensionar com o time de CDF se o RAW é o destino adequado nessa ordem de grandeza, ou se parte do processamento deveria ficar no Databricks antes da extração.

---

## Estado da implementação

Já aplicado em `config.yaml`:

- Tag de tier no comentário das 149 queries, no formato `# TABELA - descrição [TIER n | contagem linhas]`.
- Tier 1 sem `WHERE` e sem `LIMIT 10` (51 queries), conforme a regra R2c do `PRD.md`.
- Tier 2 com filtro sargável de 1 ano e sem `LIMIT 10` (33 queries).

Fica para uma etapa posterior, após a discussão com o time:

- Reescrever o filtro para a forma sargável e remover o `LIMIT 10` nas 65 queries restantes (Tiers 3–5 + sem contagem).
- Configurar a seção `extractor` com `state-store`, e definir `parallelism` e `upload-queue-size`.
- Adicionar `incremental-field`, `initial-start` e `schedule` nas queries dos Tiers 3, 4 e 5.
- Definir a projeção de colunas das views dos Tiers 4 e 5.
