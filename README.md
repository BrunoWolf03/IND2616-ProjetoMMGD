# MMGD no Sudeste: efeito da Lei 14.300 e projeção até 2030

Trabalho da disciplina IND2616 Séries Temporais (PUC-Rio, DEI).
Gustavo Riedel, Bruno Wolf e Enzo Dux.

![Potência de MMGD adicionada por mês no Sudeste](resultados/aneel_serie_mensal_sudeste.png)

## Objetivo

A micro e minigeração distribuída (MMGD), quase toda solar em telhados, passou de
~20 MW instalados por ano no Sudeste em 2016 para mais de 3 GW por ano em 2024. A Lei 14.300,
de jan/2022, mudou a compensação de energia. Quem protocolou o pedido até jan/2023
manteve a regra antiga, e os demais passam a pagar o uso da rede de forma gradual até 2029.

O trabalho responde três perguntas para SP, MG, RJ e ES:

1. **Difusão:** como evolui a potência adicionada por mês em cada estado?
2. **Efeito da lei:** quanto da corrida de 2022–2023 e da queda depois dela se deve à Lei 14.300,
   separando isso da desaceleração que a série já mostrava?
3. **2030:** quanta potência o Sudeste terá instalada em 2030 e, com isso, quanto a MMGD
   reduz a carga da rede ao meio-dia?

## Plano

A referência metodológica é Hyndman & Athanasopoulos, *Forecasting: Principles and Practice*,
3ª ed. (FPP3). Entre parênteses, as seções do livro usadas em cada fase.

| # | Fase | Método | Saída | Situação |
|---|---|---|---|---|
| 1 | Dados | extração com cache, checagens, série mensal por UF | `data/processed/` | feito |
| 2 | Exploração e identificação | transformação log (§3.1), decomposição STL (§3.6), ACF/PACF (§2.8), testes de raiz unitária e diferenciação (§9.1), força de tendência e sazonalidade (§4.3) | transformação, ordens de diferenciação e se há termo sazonal, por UF | próximo |
| 3 | Efeito da lei | regressão com erros ARIMA (cap. 10) com variáveis de intervenção (§7.4): *spike*/degrau para a corrida (jan/2022 a meados de 2023) e mudança de nível e de inclinação depois; leitura como análise de intervenção (Box & Tiao, 1975) | efeito estimado da lei por UF, com intervalo, e diagnóstico de resíduos (§5.4) | a fazer |
| 4 | Comparação de modelos | naïve e drift (§5.2), ETS (cap. 8), ARIMA (cap. 9) e o modelo com intervenção; Bass (1969) como referência de difusão; validação cruzada temporal (§5.10) com métricas pontuais e de distribuição (§5.8–5.9) | modelo escolhido por UF | a fazer |
| 5 | Cenários para 2030 | previsões retransformadas com correção de viés (§5.6); dependência entre estados por cópula nos resíduos e simulação conjunta, comparada com a reconciliação hierárquica SP+MG+RJ+ES = Sudeste (cap. 11, §13.5) | leque da potência acumulada do Sudeste em 2030 | a fazer |
| 6 | Impacto na rede | cenários de 2030 × perfil horário de radiação (INMET) contra a carga do ONS | carga líquida ao meio-dia em 2030 | a fazer |

O que os dados já mostram (fase 1):

- O ano de 2024 é o maior da série no Sudeste (3,5 GW). A queda só aparece em 2025, então o efeito da lei não é imediato.
- Em escala log, o crescimento de todos os estados já desacelerava antes de 2022 ([gráfico](resultados/aneel_serie_log_uf.png)).
- A sazonalidade é fraca ([mapa de calor](resultados/aneel_sazonalidade_sudeste.png)). A fase 2 testa se o termo sazonal é necessário.

## Dados

Nada de dados vai para o git (`data/` está no `.gitignore`). Tudo é baixado das fontes oficiais na primeira execução.

| Fonte | O que traz | Granularidade | Período |
|---|---|---|---|
| [ANEEL, empreendimentos de MMGD](https://dadosabertos.aneel.gov.br/dataset/relacao-de-empreendimentos-de-geracao-distribuida) | cada usina: UF, potência, data de conexão, dados técnicos | usina (retrato atual do cadastro) | conexões desde 2004; a série usa jan/2016 em diante |
| [ONS, carga verificada](https://apicarga.ons.org.br) | carga global, carga líquida e MMGD estimada por área | 30 min | fev/2019 em diante |
| [INMET, estações automáticas](https://portal.inmet.gov.br/dadoshistoricos) | radiação global e temperatura | hora, por estação | 2019 em diante |

Decisões de tratamento da série dos modelos (detalhes no topo de [`src/build_series.py`](src/build_series.py)):

- **Eixo temporal = `DatConexao`** do arquivo técnico fotovoltaico, que cruza com 99,98% das usinas do Sudeste.
- **Duplicatas descartadas.** São usinas idênticas com código diferente, compatíveis com a migração SISGD → MMGD, e somam 2,75% dos MW do Sudeste.
- **Os 2 últimos meses do retrato são cortados** por atraso de cadastro. A tabela em `data/interim/checks_aneel/cauda_retrato.csv` mostra se o corte ainda vale num retrato novo.
- **Viés de sobrevivência:** o cadastro só traz usinas ativas, e as desativadas somem do histórico.

## Organização do repositório

```
src/                    pipeline de dados: um script por etapa
  main.py               roda as etapas em ordem
  extract_ons.py        etapa ons
  extract_inmet.py      etapa inmet
  extract_aneel.py      etapa aneel
  preprocess_aneel.py   etapa preproc
  build_series.py       etapa serie
  estilo.py             cores e estilo comuns dos gráficos
notebooks/              exploração e análise
resultados/             gráficos gerados pelos scripts (versionados)
data/                   não versionado, criado pelo main.py
  raw/                  arquivos como vieram das fontes
  interim/              bases intermediárias e checagens (checks_*)
  processed/            bases prontas para análise e modelos
```

Cada etapa é um script independente, com uma função `main()` e opções de linha de comando
(`python src/extract_ons.py --help`). O `main.py` só chama as etapas na ordem. Todas usam
cache: rodar de novo só baixa e refaz o que falta.

| Etapa | Script | Faz | Saída principal |
|---|---|---|---|
| `ons` | `extract_ons.py` | baixa a carga do ONS (SP, MG, RJ, ES), checa lacunas e duplicatas, agrega por hora | `data/processed/ons_carga_horaria.parquet` |
| `inmet` | `extract_inmet.py` | baixa as estações do Sudeste, trata a radiação, agrega por UF | `data/processed/inmet_horario_por_uf.parquet` |
| `aneel` | `extract_aneel.py` | baixa o cadastro de MMGD e o arquivo técnico fotovoltaico | `data/raw/aneel/*.parquet` |
| `preproc` | `preprocess_aneel.py` | remove datas inválidas e marca duplicatas | `data/interim/aneel_gd_preproc.parquet` |
| `serie` | `build_series.py` | monta a série mensal por UF | `data/processed/aneel_serie_mensal_uf.parquet` |

A série dos modelos tem uma linha por mês × UF (SP, MG, RJ, ES e `SE` = soma das quatro),
com as colunas `mes`, `uf`, `mw` (potência adicionada no mês), `n_usinas`, `mw_acumulado` e `log_mw`:

```python
import pandas as pd
serie = pd.read_parquet("data/processed/aneel_serie_mensal_uf.parquet")
sp = serie[serie.uf == "SP"].set_index("mes")["log_mw"].asfreq("MS")
```

## Como rodar

```sh
git clone https://github.com/BrunoWolf03/IND2616-ProjetoMMGD.git
cd IND2616-ProjetoMMGD

python -m venv .venv
source .venv/bin/activate        # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt

python src/main.py                         # tudo (~10 min na primeira vez, ~1,5 GB)
python src/main.py --etapas preproc serie  # só algumas etapas
python src/main.py --refresh               # baixa de novo ONS e ANEEL (retratos mais recentes)
```

Para os notebooks, selecione o kernel do `.venv` no VSCode ou no Jupyter.

Se instalar um pacote novo, acrescente-o ao `requirements.txt` com a versão fixada.

## Referências

- Hyndman, R. J. & Athanasopoulos, G. (2021). *Forecasting: Principles and Practice*, 3ª ed. OTexts. https://otexts.com/fpp3/
- Bass, F. M. (1969). A new product growth for model consumer durables. *Management Science*, 15(5).
- Box, G. E. P. & Tiao, G. C. (1975). Intervention analysis with applications to economic and environmental problems. *JASA*, 70(349).
- Sklar, A. (1959). Fonctions de répartition à n dimensions et leurs marges.
- Brasil. Lei nº 14.300, de 6 de janeiro de 2022 (marco legal da MMGD).
