"""Série mensal de potência de MMGD adicionada por UF no Sudeste (entrada dos modelos).

Entradas:
- data/interim/aneel_gd_preproc.parquet (saída de preprocess_aneel.py)
- data/raw/aneel/empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet (DatConexao)

Decisões:
- Eixo temporal = DatConexao do arquivo técnico fotovoltaico (cruza com 99,98% das
  usinas do Sudeste). A data cadastral da base principal infla 2016-2017 em ~30%.
  Usinas sem DatConexao (não fotovoltaicas ou sem cruzamento) ficam de fora.
- Duplicatas: as cópias marcadas em dup_candidato são descartadas. Pesam 2,75% dos
  MW do Sudeste, espalhadas por toda a série (1-4% na maioria dos meses).
- Fim da série: os últimos meses do retrato vêm incompletos (no retrato de out/2026,
  julho tem ~70% do nível dos meses anteriores e agosto quase nada). Cortamos
  MESES_INCOMPLETOS meses antes do último mês com conexão; a tabela de cauda
  impressa (e salva em checks_aneel/) mostra se o corte continua bom num retrato novo.
- Início da série: jan/2016 (REN 687). O acumulado inclui o estoque anterior.

Saída: data/processed/aneel_serie_mensal_uf.parquet, formato longo, uma linha por
mês x UF (SP, MG, RJ, ES e SE = soma das quatro), com as colunas
  mes, uf, mw (adicionada no mês), n_usinas, mw_acumulado, log_mw
Checagens em data/interim/checks_aneel/ e gráficos em resultados/aneel_*.png.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

import estilo
from estilo import CORES_UF, UFS

ROOT = Path(__file__).resolve().parents[1]
INICIO = pd.Period("2016-01", "M")
MESES_INCOMPLETOS = 2
COL_POT = "MdaPotenciaInstaladaKW"

# marcos regulatórios desenhados nos gráficos
MARCOS = {
    "REN 687": pd.Timestamp("2016-03-01"),
    "Lei 14.300": pd.Timestamp("2022-01-07"),
    "fim da janela": pd.Timestamp("2023-01-07"),
}
CORRIDA = (pd.Timestamp("2022-01-01"), pd.Timestamp("2023-07-01"))

log = logging.getLogger("build_series")


def load(preproc: Path, tecnico: Path) -> pd.DataFrame:
    """Usinas do Sudeste com mês de conexão e flag de duplicata."""
    if not preproc.exists():
        raise FileNotFoundError(f"{preproc} não existe; rode antes: python src/main.py --etapas aneel preproc")
    base = pd.read_parquet(preproc, columns=["CodEmpreendimento", "SigUF", COL_POT, "dup_candidato"])
    base = base[base["SigUF"].isin(UFS)]
    tec = pd.read_parquet(tecnico, columns=["CodGeracaoDistribuida", "DatConexao"])
    df = base.merge(tec, left_on="CodEmpreendimento", right_on="CodGeracaoDistribuida", how="left")
    df["mes"] = pd.to_datetime(df["DatConexao"], errors="coerce").dt.to_period("M")
    sem = df["mes"].isna()
    log.info("Sudeste: %d usinas, %.2f GW; sem DatConexao: %d (%.3f%% dos MW, descartadas)",
             len(df), df[COL_POT].sum() / 1e6, sem.sum(), 100 * df.loc[sem, COL_POT].sum() / df[COL_POT].sum())
    return df[~sem]


def mensal(df: pd.DataFrame) -> pd.DataFrame:
    """MW e nº de usinas por mês x UF, meses sem conexão preenchidos com zero."""
    g = df.groupby(["mes", "SigUF"]).agg(mw=(COL_POT, "sum"), n_usinas=(COL_POT, "size"))
    g["mw"] /= 1e3
    idx = pd.MultiIndex.from_product(
        [pd.period_range(df["mes"].min(), df["mes"].max(), freq="M"), UFS], names=["mes", "uf"])
    return g.rename_axis(["mes", "uf"]).reindex(idx, fill_value=0)


def tabela_cauda(m: pd.DataFrame, n: int = 6) -> pd.DataFrame:
    """Últimos meses do retrato em relação à mediana dos 6 meses anteriores (Sudeste)."""
    se = m["mw"].groupby(level="mes").sum()
    ref = se.shift(n).rolling(6).median()
    return pd.DataFrame({"mw_sudeste": se, "razao_vs_mediana_6m_antes": se / ref}).tail(n + 2)


def efeito_duplicatas(com: pd.DataFrame, sem: pd.DataFrame) -> pd.DataFrame:
    a = com["mw"].groupby([com.index.get_level_values("mes").year, "uf"]).sum().unstack()
    b = sem["mw"].groupby([sem.index.get_level_values("mes").year, "uf"]).sum().unstack()
    out = pd.concat({"mw_com_dup": a[UFS], "mw_sem_dup": b[UFS]}, axis=1)
    out[("pct_removido", "SE")] = 100 * (1 - b[UFS].sum(axis=1) / a[UFS].sum(axis=1))
    return out.rename_axis("ano")


def montar(m: pd.DataFrame, ultimo: pd.Period) -> pd.DataFrame:
    """Recorta [INICIO, ultimo], soma o Sudeste e calcula acumulado e log."""
    wide_mw = m["mw"].unstack()[UFS]
    wide_n = m["n_usinas"].unstack()[UFS]
    wide_mw["SE"], wide_n["SE"] = wide_mw.sum(axis=1), wide_n.sum(axis=1)
    acum = wide_mw.cumsum()  # inclui o estoque anterior a INICIO
    janela = slice(INICIO, ultimo)
    out = pd.concat({"mw": wide_mw.loc[janela], "n_usinas": wide_n.loc[janela],
                     "mw_acumulado": acum.loc[janela]}, axis=1).stack(level=1, future_stack=True)
    out.index.names = ["mes", "uf"]
    out = out.reset_index()
    if (out["mw"] <= 0).any():
        raise ValueError(f"meses sem potência adicionada, log indefinido:\n{out[out['mw'] <= 0]}")
    out["log_mw"] = np.log(out["mw"])
    out["n_usinas"] = out["n_usinas"].astype("int64")
    out["mes"] = out["mes"].dt.to_timestamp()
    out["uf"] = pd.Categorical(out["uf"], categories=UFS + ["SE"])
    return out.sort_values(["uf", "mes"]).reset_index(drop=True)


# ------------------------------------------------------------------- gráficos

def _marcos(ax, rotulos: bool = True) -> None:
    ax.axvspan(*CORRIDA, color=estilo.DESTAQUE, zorder=0, lw=0)
    for nome, d in MARCOS.items():
        ax.axvline(d, color=estilo.TINTA_SUAVE, lw=0.9, ls=(0, (3, 3)), zorder=1)
        if rotulos:  # acima da área do gráfico, para não cobrir dados nem legenda
            ax.annotate(nome, (d, 1), xycoords=("data", "axes fraction"), xytext=(0, 3),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=8.5, color=estilo.TINTA_SUAVE)


def _legenda_embaixo(ax, h=None, l=None) -> None:
    h, l = (h, l) if h else ax.get_legend_handles_labels()
    ax.legend(h, l, loc="upper left", bbox_to_anchor=(0, -0.08), ncol=len(h), fontsize=9.5,
              handlelength=1.6, columnspacing=1.4)


def _eixo_anos(ax) -> None:
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.margins(x=0.01)


def plot_mensal(serie: pd.DataFrame, m: pd.DataFrame, out: Path, fonte: str) -> None:
    """Barras empilhadas por UF; os meses cortados aparecem em cinza hachurado."""
    w = serie[serie["uf"] != "SE"].pivot(index="mes", columns="uf", values="mw")[UFS]
    fora = m["mw"].unstack()[UFS].sum(axis=1)
    fora = fora[fora.index > serie["mes"].max().to_period("M")]
    fig, ax = plt.subplots(figsize=(11, 5))
    _marcos(ax)
    base = np.zeros(len(w))
    for uf in UFS:
        ax.bar(w.index, w[uf], bottom=base, width=24, color=CORES_UF[uf], label=uf,
               edgecolor="white", linewidth=0.3)
        base += w[uf].to_numpy()
    ax.bar(fora.index.to_timestamp(), fora, width=24, color="white", edgecolor=estilo.NEUTRO,
           hatch="////", linewidth=0.6, label="incompleto (fora da série)")
    ax.set_title("Potência de MMGD adicionada por mês no Sudeste", pad=24)
    ax.set_ylabel("MW por mês")
    _eixo_anos(ax)
    h, l = ax.get_legend_handles_labels()
    h.append(Patch(color=estilo.DESTAQUE)); l.append("corrida pré-Lei 14.300")
    _legenda_embaixo(ax, h, l)
    estilo.fonte_rodape(fig, fonte)
    fig.savefig(out)
    plt.close(fig)


def plot_log_uf(serie: pd.DataFrame, out: Path, fonte: str) -> None:
    """Uma UF por painel, escala log, com média móvel de 12 meses."""
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.5), sharex=True)
    for ax, uf in zip(axes.flat, UFS):
        s = serie[serie["uf"] == uf].set_index("mes")["mw"]
        _marcos(ax, rotulos=False)
        ax.plot(s.index, s, color=CORES_UF[uf], lw=1.2, alpha=0.55)
        ax.plot(s.index, s.rolling(12, center=True).mean(), color=CORES_UF[uf], lw=2.2)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.set_title(uf, fontsize=12)
        _eixo_anos(ax)
        ax.tick_params(axis="x", labelsize=9)
    for ax in axes[:, 0]:
        ax.set_ylabel("MW por mês (log)")
    fig.suptitle("Potência adicionada por UF, em escala log (linha grossa: média móvel de 12 meses)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    estilo.fonte_rodape(fig, fonte + " Faixa: corrida pré-Lei 14.300.")
    fig.savefig(out)
    plt.close(fig)


def plot_acumulado(serie: pd.DataFrame, out: Path, fonte: str) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    _marcos(ax)
    for uf in UFS:
        s = serie[serie["uf"] == uf].set_index("mes")["mw_acumulado"] / 1e3
        ax.plot(s.index, s, color=CORES_UF[uf], lw=2.2, label=uf)
        ax.annotate(f"{uf}  {s.iloc[-1]:.1f} GW", (s.index[-1], s.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=10, color=estilo.TINTA)
    ax.set_title("Potência instalada acumulada de MMGD por UF", pad=24)
    ax.set_ylabel("GW")
    _eixo_anos(ax)
    ax.set_xlim(right=serie["mes"].max() + pd.DateOffset(months=10))
    _legenda_embaixo(ax)
    estilo.fonte_rodape(fig, fonte)
    fig.savefig(out)
    plt.close(fig)


def plot_sazonalidade(serie: pd.DataFrame, out: Path, fonte: str) -> None:
    """Mapa de calor ano x mês do Sudeste."""
    s = serie[serie["uf"] == "SE"].set_index("mes")["mw"]
    tab = s.groupby([s.index.year, s.index.month]).sum().unstack()
    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.grid(False)
    im = ax.imshow(tab, cmap="Blues", aspect="auto")
    ax.set_xticks(range(12), ["jan", "fev", "mar", "abr", "mai", "jun",
                              "jul", "ago", "set", "out", "nov", "dez"])
    ax.set_yticks(range(len(tab)), tab.index)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    lim = np.nanmax(tab.to_numpy()) * 0.6
    for (i, j), v in np.ndenumerate(tab.to_numpy()):
        if not np.isnan(v):
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8,
                    color="white" if v > lim else estilo.TINTA)
    ax.set_title("Sudeste: MW adicionados por mês e ano")
    cb = fig.colorbar(im, ax=ax, shrink=0.8, label="MW")
    cb.outline.set_visible(False)
    estilo.fonte_rodape(fig, fonte)
    fig.savefig(out)
    plt.close(fig)


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--preproc", type=Path, default=ROOT / "data" / "interim" / "aneel_gd_preproc.parquet")
    p.add_argument("--tecnico", type=Path, default=ROOT / "data" / "raw" / "aneel"
                   / "empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet")
    p.add_argument("--output", type=Path, default=ROOT / "data" / "processed" / "aneel_serie_mensal_uf.parquet")
    p.add_argument("--ultimo-mes", type=pd.Period, default=None,
                   help=f"último mês da série (aaaa-mm); padrão: último mês com conexão - {MESES_INCOMPLETOS}")
    args = p.parse_args(argv)
    logging.basicConfig(force=True, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 200)

    df = load(args.preproc, args.tecnico)
    m_com = mensal(df)
    m = mensal(df[~df["dup_candidato"]])
    ultimo = args.ultimo_mes or (df["mes"].max() - MESES_INCOMPLETOS)
    log.info("último mês com conexão: %s; série vai até %s", df["mes"].max(), ultimo)

    checks = ROOT / "data" / "interim" / "checks_aneel"
    checks.mkdir(parents=True, exist_ok=True)
    cauda = tabela_cauda(m)
    print("\n=== Cauda do retrato (Sudeste, sem duplicatas) ===")
    print(cauda.round(2).to_string())
    cauda.to_csv(checks / "cauda_retrato.csv")
    dup = efeito_duplicatas(m_com, m).loc[INICIO.year:ultimo.year]
    print("\n=== Efeito de remover as duplicatas (MW por ano) ===")
    print(dup.round(1).to_string())
    dup.to_csv(checks / "efeito_duplicatas.csv")

    serie = montar(m, ultimo)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serie.to_parquet(args.output, index=False)
    se = serie[serie["uf"] == "SE"]
    log.info("salvo %s: %d linhas, %s a %s, acumulado SE %.2f GW", args.output, len(serie),
             f"{se['mes'].min():%Y-%m}", f"{se['mes'].max():%Y-%m}", se["mw_acumulado"].iloc[-1] / 1e3)

    estilo.aplicar()
    res = ROOT / "resultados"
    res.mkdir(exist_ok=True)
    fonte = (f"Fonte: ANEEL, cadastro de MMGD (retrato até {df['mes'].max()}), data de conexão, "
             "sem duplicatas.")
    plot_mensal(serie, m, res / "aneel_serie_mensal_sudeste.png", fonte)
    plot_log_uf(serie, res / "aneel_serie_log_uf.png", fonte)
    plot_acumulado(serie, res / "aneel_acumulado_uf.png", fonte)
    plot_sazonalidade(serie, res / "aneel_sazonalidade_sudeste.png", fonte)
    log.info("gráficos salvos em %s (aneel_*.png)", res)


if __name__ == "__main__":
    main()
