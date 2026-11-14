# MMGD no Sudeste: efeito da Lei 14.300 e projeção até 2030

Trabalho da disciplina IND2616 Séries Temporais (PUC-Rio, DEI).
Gustavo Riedel, Bruno Wolf e Enzo Dux.

## Objetivo

A micro e minigeração distribuída (MMGD), quase toda solar em telhados, passou de
~20 MW instalados por ano no Sudeste em 2016 para mais de 3 GW por ano em 2024. A Lei 14.300,
de jan/2022, mudou a compensação de energia. Quem protocolou o pedido até jan/2023
manteve a regra antiga. Os demais passam a pagar o uso da rede (Fio B) de forma gradual,
de 15% em 2023 a 90% em 2028. A partir de 2029 vale uma regra nova, que a ANEEL ainda não definiu.

O trabalho responde três perguntas para SP, MG, RJ e ES:

1. **Difusão:** como evolui a potência adicionada por mês em cada estado?
2. **Efeito da lei:** quanto da corrida de 2022–2023 e da queda depois dela se deve à Lei 14.300,
   separando isso da desaceleração que a série já mostrava?
3. **2030:** quanta potência o Sudeste terá instalada em 2030 e, com isso, quanto a MMGD
   reduz a carga da rede ao meio-dia? E quanto isso muda se a regra de 2029 provocar um choque
   como o do fim da janela da lei em 2023, ou metade, ou o dobro dele?

## Plano

A referência metodológica é Hyndman & Athanasopoulos, *Forecasting: Principles and Practice*,
3ª ed. (FPP3). Entre parênteses, as seções do livro usadas em cada fase.

| # | Fase | Método | Saída | Situação |
|---|---|---|---|---|
| 1 | Dados | extração com cache, checagens, série mensal por UF | `data/processed/` | feito |
| 2 | Exploração e identificação | transformação log (§3.1), decomposição STL (§3.6), ACF/PACF (§2.8), testes de raiz unitária e diferenciação (§9.1), força de tendência e sazonalidade (§4.3) | transformação, ordens de diferenciação e se há termo sazonal, por UF ([notebook](notebooks/02_identificacao.ipynb)) | feito |
| 3 | Efeito da lei | regressão com erros ARIMA (cap. 10) sobre o log, com variáveis de intervenção (§7.4) em datas fixadas pelo calendário: mudança na taxa de crescimento em abr/2020 (desaceleração anterior à lei) e na corrida (jan/2022 a jun/2023), e queda de nível e mudança de taxa depois dela; leitura como análise de intervenção (Box & Tiao, 1975). Ordens por AICc em torno do ARIMA(0,1,1). Robustez: contrafactual ajustado até dez/2021 e fim da corrida em jan/2023 | efeito estimado da lei por UF, com intervalo, e diagnóstico de resíduos (§5.4) ([notebook](notebooks/03_intervencao.ipynb)); variante com degraus na taxa, contrafactuais em GW e teste de ordens maiores ([notebook 03b](notebooks/03b_intervencao_degraus.ipynb)) | feito |
| 4 | Comparação de modelos | naïve e drift (§5.2), ETS (cap. 8), ARIMA (cap. 9) e o modelo com intervenção, com d = 1 e com tendência quebrada e d = 0; Bass (1969) como referência de difusão; validação cruzada temporal (§5.10) com métricas pontuais e de distribuição (§5.8–5.9) e calibração pelo PIT | modelo escolhido por UF ([notebook](notebooks/04_comparacao.ipynb)) | feito |
| 5 | Cenários para 2030 | previsões retransformadas com correção de viés (§5.6); dependência entre estados por cópula nos resíduos e simulação conjunta, cuja soma dá o Sudeste, comparada com o Sudeste direto e a reconciliação MinT (cap. 11, §13.5). Cenários para a regra de 2029: base (sem choque) e 0,5×, 1× e 2× o choque de jul/2023 estimado na fase 3 (queda de nível mais mudança de taxa contra o ritmo de 2020–21, modelo com d = 0), aplicado a partir de jan/2029; sensibilidades com d = 1 e com o efeito sorteado | leque da potência acumulada do Sudeste em 2030, por cenário ([notebook](notebooks/05_cenarios_2030.ipynb)) | feito |
| 6 | Impacto na rede | geração = caminhos de 2030 × radiação horária do INMET × fator calibrado na MMGD estimada pelo ONS (validado fora da amostra em 2025); carga global do ONS projetada por ETS/SARIMA/ingênuos com validação cruzada de 24 meses (§5.10), em dois cenários (estável e crescendo ~2% ao ano), com o perfil horário do mesmo ano de clima; carga líquida = global − geração | carga líquida ao meio-dia, rampa de 3 h e curva do pato em 2030, por cenário, com as fontes de incerteza comparadas ([notebook](notebooks/06_impacto_rede.ipynb)) | feito |

Os multiplicadores 0,5×, 1× e 2× são escolhidos, não estimados: o histórico tem um único episódio de mudança de regra.
Fica como trabalho futuro estimar a resposta do mercado ao custo a partir dos degraus anuais do Fio B (15% → 90%),
o que permitiria traduzir uma regra concreta em efeito. Isso exige a composição tarifária por distribuidora, e o sinal
tende a ser fraco: são poucos degraus, todos em janeiro.

O que os dados já mostram (fases 1 e 2):

- O ano de 2024 é o maior da série no Sudeste (3,5 GW). A queda só aparece em 2025, então o efeito da lei não é imediato.
- Em escala log, o crescimento de todos os estados já desacelerava antes de 2022 (`resultados/aneel_serie_log_uf.png`).
- A sazonalidade é fraca: a força sazonal fica no máximo em 0,33, abaixo do limiar de 0,64 do FPP3, então não há diferença sazonal.
- Em todas as UFs o log tem uma raiz unitária (d = 1), e a diferença se comporta como MA(1). O ponto de partida é um ARIMA(0,1,1).
- O teste de Zivot-Andrews põe a quebra mais forte na taxa de crescimento de SP em jan/2023 (fim da janela da lei), e a de MG e do Sudeste em jan/2020, antes da lei. O modelo da fase 3 precisa separar esses dois movimentos.

O que a fase 3 mostra:

- A desaceleração começou em 2020, antes da lei: o crescimento do Sudeste cai de ~200% para ~40% ao ano.
- A corrida de 2022–2023 não acelerou o crescimento (exceto em MG), e depois dela o crescimento para (~4% ao ano no Sudeste).
- Em jun/2026, a potência adicionada por mês no Sudeste está ~70% abaixo do que o ritmo de 2020–2021 daria. O número se mantém no contrafactual, com outro fim de corrida e com d = 0. É um teto, porque o modelo não separa a lei de saturação e juros.
- A significância depende de d: com d = 1, só MG tem efeito significativo; com tendência quebrada e d = 0, SP, RJ e Sudeste também. A fase 4 decide entre os dois.
- O AICc prefere o fim da corrida em jan/2023 em SP e no Sudeste, e em jun/2023 em MG. O modelo mantém jun/2023, porque a data
  vem do atraso entre protocolo e conexão e não do ajuste, e o efeito total quase não muda. Jan/2023 fica como sensibilidade.
- A variante com degraus na taxa (notebook 03b) chega à mesma leitura. A corrida acrescentou de 0,5 a 1,0 GW frente aos contrafactuais sem lei, e de jul/2023 a jun/2026 faltam de 5 a 8 GW. Os resíduos de MG e ES têm autocorrelação perto do lag 10 que ordens até ARMA(3,3) não eliminam, e o efeito estimado não muda com a ordem.

O que as fases 4 e 5 mostram:

- **O modelo escolhido é a combinação ETS + Bass**, com o menor CRPS em SP, ES e no Sudeste e viés de 1% na soma de 12 meses do Sudeste. Os modelos isolados erram em sentidos opostos: os de tendência preveem demais e o Bass, de menos. No modelo de intervenção, a cobertura baixa de MG e ES na validação cruzada vem de viés, e não de intervalos estreitos.
- **2030:** o Sudeste deve ter cerca de **26,5 GW instalados em dez/2030** (80%: 22,8 a 31,1 GW), contra 16,7 GW em jun/2026, com 2 a 2,5 GW adicionados por ano. A cópula entre os estados alarga o leque em 17%. A soma dos estados, a previsão direta e a reconciliação MinT diferem em menos de 0,5 GW. Conforme o modelo, a média vai de 18,7 GW (Bass) a 34,3 GW (ETS).
- **Regra de 2029:** se ela repetir o choque de jul/2023 a partir de jan/2029, o Sudeste perde **entre 0 e 2,4 GW** em dez/2030 (até 3,4 GW no 2×). O teto mede a mudança de taxa contra o ritmo de 2020–21 (e não contra o da corrida, inflado pela antecipação em MG); ele inclui juros e saturação. O piso mede o choque contra o próprio modelo da base (ETS + Bass ajustado até jun/2023) e dá ~0, porque SP ficou acima e MG abaixo do previsto depois de 2023 (`resultados/cenarios_2029_choque_se.png`).

O que a fase 6 mostra:

- **Em 2030 a MMGD deve cobrir ~39% da carga do Sudeste ao meio-dia** (80%: 32% a 46%), contra 22% em 2025. A carga líquida às 12h–13h cai de 29,3 para 23,5 GW.
- **A curva do pato chega em qualquer modelo:** o meio-dia vira o vale da rede (profundidade de +5,8 GW, contra −0,35 GW em 2025; vale ao meio-dia em 96% dos dias, contra 36%) e a rampa de 3 horas do fim da tarde sobe de 6,5 para 9,3 GW.
- **A maior incerteza é o modelo da potência instalada:** com os outros modelos plausíveis da fase 5, a parcela ao meio-dia vai de 29% (Bass) a 49% (ETS). A carga domina a incerteza da carga líquida em GW: se voltar a crescer ~2% ao ano, a parcela cai para 36%. **A regra de 2029 é a menor das fontes** (teto 2×: 35%).
- A geração calibrada reproduz a MMGD estimada pelo ONS fora da amostra (2025: R² de 0,93 a 0,97). Uma auditoria independente refez as contas com código próprio e chegou aos mesmos números.
- MG e ES chegam a ~60% da carga coberta pela MMGD ao meio-dia; SP e RJ, a 26–32%.

## Limitações conhecidas

**Dados**
- O cadastro da ANEEL só traz usinas ativas: as desativadas somem do histórico (viés de sobrevivência). Os dois últimos meses do
  retrato são cortados por atraso de cadastro, e retratos de datas diferentes mudam a série em até ~100 MW (out/2026: 16,75 contra
  16,65 GW acumulados em jun/2026).
- A geração da MMGD do ONS não é medida: é estimada a partir de irradiância. A calibração da fase 6 reproduz essa estimativa, e
  não a geração real. Um teste com a carga medida (global − MMGD) às 11h–12h não indica que o ONS superestime a MMGD; se algo, ela
  pode estar subestimada em SP e no RJ.
- A radiação é a média das estações do INMET em cada UF, e não a dos locais onde estão os painéis.

**Efeito da lei (fases 2 e 3)**
- O efeito estimado inclui tudo o que mudou junto com a lei (juros de 2022–23, preço dos módulos, saturação dos primeiros
  clientes). Por isso ele é um teto.
- As datas de quebra são fixadas pelo calendário da lei e pela fase 2, e não estimadas. A data do Zivot-Andrews para 2020 é
  instável (jan/2020 no retrato atual, mar–abr/2020 no anterior), mas o efeito em 2030 não muda com ela (23,97 a 24,14 GW no
  choque 1×, com a rampa entre jan e jun/2020).
- Com d = 1, só MG tem efeito significativo; com d = 0, SP, RJ e o Sudeste também. A validação cruzada prefere d = 0, mas a
  conclusão "a lei teve efeito significativo" depende dessa escolha.
- Os resíduos de MG e ES têm autocorrelação perto do lag 10 que ordens até ARMA(3,3) não eliminam. O MA(1) de MG fica em θ = −1.
- Várias decisões foram tomadas depois de ver os dados (fim da corrida, d, definição do choque). Todas estão registradas com a
  sensibilidade correspondente em `design.md`, mas os p-valores não corrigem essa multiplicidade.

**Projeção da potência (fases 4 e 5)**
- A validação cruzada da fase 4 tem 13 origens de um só ano (jun/2024 a jun/2025), com horizonte de 12 meses, todas no regime
  depois da corrida. A projeção vai a 54 meses e não há dados para validar horizontes maiores: um horizonte de 24 meses deixaria
  uma única origem.
- Os leques do ETS e do Bass não incluem a incerteza dos parâmetros (o $m$ do Bass, o amortecimento $\phi$ do ETS). A troca de
  modelo (seção 5 do notebook 05) cobre isso só em parte: de 18,7 GW (Bass) a 34,3 GW (ETS) em dez/2030.
- O $m$ do Bass (19,1 GW no Sudeste) está provavelmente subestimado, se parte do pico de 2024 veio da lei e não da saturação.
- A transição do Fio B (15% → 90% até 2028) não entra de forma explícita; ela age pela tendência que os modelos estimaram.
- Os cenários da regra de 2029 vão de um teto (o choque contra o ritmo de 2020–21) a um piso ≈ 0 (o choque contra o próprio
  modelo da base). Os multiplicadores 0,5×, 1× e 2× são escolhidos, não estimados: há um só episódio de mudança de regra.

**Impacto na rede (fase 6)**
- A carga projetada não reage à MMGD (efeito rebote, tarifa horária) nem à eletrificação (carros elétricos, ar-condicionado),
  que aumentaria a carga da noite.
- A validação cruzada de 24 meses não separa bem a carga estável da carga crescendo (CRPS de 0,052 contra 0,053), e os intervalos
  da carga cobrem só ~60% dos meses no intervalo de 80%. Por isso a carga entra em dois cenários.
- Não há perda de eficiência dos painéis com a idade (~0,5% ao ano; −2% na geração tira ~0,8 ponto da parcela ao meio-dia),
  corte de geração (*curtailment*) nem baterias atrás do medidor. Feriados não são tratados no casamento dos dias.
- O clima de 2030 é sorteado de só cinco anos (2021–2025); anos mais extremos não entram no leque.
- A fase 6 usa 1.000 dos 4.000 caminhos da fase 5.

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
  modelos.py            modelos das fases 4 e 5 (e do notebook 03b): candidatos, validação cruzada, cópula, reconciliação
  estilo.py             cores e estilo comuns dos gráficos
notebooks/              exploração e análise
  02_identificacao.ipynb  fase 2: STL, ACF/PACF, raiz unitária, quebras
  03_intervencao.ipynb    fase 3: regressão com erros ARIMA e efeito da lei
  03b_intervencao_degraus.ipynb  fase 3, variante: degraus na taxa, contrafactuais em GW, ordens maiores
  04_comparacao.ipynb     fase 4: validação cruzada dos candidatos e escolha do modelo
  05_cenarios_2030.ipynb  fase 5: simulação conjunta com cópula, reconciliação, cenários de 2029 e leque de 2030
  06_impacto_rede.ipynb   fase 6: geração horária calibrada, carga global até 2030, carga líquida e curva do pato
resultados/             gráficos gerados pelos scripts (não versionado, criado pelo main.py)
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
Os notebooks 03 a 05 rodam em ordem: o 05 lê o choque gravado pelo 03 e o resultado da validação cruzada do 04 (`data/interim/cv_fase4.parquet`, ~7 min para refazer), e grava os caminhos de 2030 em `data/processed/cenarios_2030_caminhos.parquet`, a entrada da fase 6.

Se instalar um pacote novo, acrescente-o ao `requirements.txt` com a versão fixada.

## Referências

- Hyndman, R. J. & Athanasopoulos, G. (2021). *Forecasting: Principles and Practice*, 3ª ed. OTexts. https://otexts.com/fpp3/
- Bass, F. M. (1969). A new product growth for model consumer durables. *Management Science*, 15(5).
- Box, G. E. P. & Tiao, G. C. (1975). Intervention analysis with applications to economic and environmental problems. *JASA*, 70(349).
- Sklar, A. (1959). Fonctions de répartition à n dimensions et leurs marges.
- Brasil. Lei nº 14.300, de 6 de janeiro de 2022 (marco legal da MMGD).
