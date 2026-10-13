"""Pré-processamento da base de empreendimentos de MMGD da ANEEL.

Entrada: data/raw/aneel/empreendimento-geracao-distribuida.parquet (retrato do cadastro,
AnmPeriodoReferencia constante) e, opcionalmente, o arquivo técnico fotovoltaico
em data/raw/aneel/ (usado só para diagnosticar duplicatas).

Etapas:
1. Remove usinas com DthAtualizaCadastralEmpreend = 1900-01-01 (data sentinela).
2. Marca possíveis duplicatas: usinas idênticas em TODAS as colunas exceto
   CodEmpreendimento. Nada é removido aqui; a decisão fica para depois.

Sobre as duplicatas (diagnóstico de out/2026):
- CodEmpreendimento é numerado em sequência no tempo dentro de cada UF, então dá
  para estimar quando cada código foi emitido.
- Na maioria dos pares, um código é compatível com a data da usina e o outro é
  muito mais novo, emitido em lotes concentrados (~mar/2026 e o lote mais recente).
  É a assinatura de recadastro em massa, compatível com a migração SISGD -> MMGD.
- Com CPF/CNPJ, CEP e nome mascarados, não dá para ter certeza caso a caso: um
  mesmo titular pode ter usinas iguais em UCs diferentes.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
UFS_SE = ["SP", "MG", "RJ", "ES"]
DATA_SENTINELA = "1900-01-01"
COL_DATA = "DthAtualizaCadastralEmpreend"
COL_COD = "CodEmpreendimento"
COL_POT = "MdaPotenciaInstaladaKW"

log = logging.getLogger("preprocess_aneel")


def load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df[COL_DATA] = pd.to_datetime(df[COL_DATA].astype(str), errors="coerce")
    return df


def remove_sentinela(df: pd.DataFrame) -> pd.DataFrame:
    """Remove usinas com data 1900-01-01 (ou data inválida)."""
    ruim = df[COL_DATA].isna() | (df[COL_DATA] == DATA_SENTINELA)
    log.info("removidas %d usinas com data %s/inválida (%.2f MW)",
             ruim.sum(), DATA_SENTINELA, df.loc[ruim, COL_POT].sum() / 1e3)
    return df[~ruim].reset_index(drop=True)


def code_number(cod: pd.Series) -> pd.Series:
    """'GD.SP.004.158.351' -> 4158351."""
    return cod.str[-11:].str.replace(".", "", regex=False).astype(np.int64)


def flag_duplicatas(df: pd.DataFrame) -> pd.DataFrame:
    """Marca grupos idênticos em todas as colunas exceto o código.

    dup_grupo: id do grupo (-1 se a usina é única)
    dup_n: tamanho do grupo
    dup_candidato: True para todos os membros do grupo menos o de menor código
                   (o mais antigo), ou seja, as cópias que uma deduplicação removeria.
    """
    key = [c for c in df.columns if c != COL_COD]
    gid = df.groupby(key, dropna=False, sort=False).ngroup()
    n = gid.map(gid.value_counts())
    num = code_number(df[COL_COD])
    menor = num.groupby(gid).transform("min")
    return df.assign(
        cod_num=num,
        dup_grupo=gid.where(n > 1, -1),
        dup_n=n,
        dup_candidato=(n > 1) & (num != menor),
    )


def estima_emissao(df: pd.DataFrame) -> pd.Series:
    """Mês estimado de emissão de cada código: primeiro mês em que o p90 dos
    números de código das usinas únicas (da mesma UF) alcança aquele número.
    Códigos acima de tudo o que já foi visto recebem o mês seguinte ao último."""
    base = df[df["dup_n"] == 1]
    mes = base[COL_DATA].dt.to_period("M")
    p90 = base.groupby(["SigUF", mes])["cod_num"].quantile(0.9).groupby(level=0).cummax()
    out = pd.Series(pd.NaT, index=df.index, dtype="period[M]")
    for uf, g in df.groupby("SigUF"):
        s = p90.loc[uf]
        pos = np.searchsorted(s.to_numpy(), g["cod_num"].to_numpy())
        meses = np.append(s.index.to_numpy(), s.index[-1] + 1)
        out.loc[g.index] = meses[pos]
    return out


def resumo_duplicatas(df: pd.DataFrame, tecnico: Path | None) -> None:
    d = df[df["dup_n"] > 1]
    cand = df[df["dup_candidato"]]
    print("\n=== Possíveis duplicatas (idênticas exceto CodEmpreendimento) ===")
    print(f"usinas em grupos: {len(d):,}  grupos: {d['dup_grupo'].nunique():,}  "
          f"cópias (candidatas a remover): {len(cand):,} = {cand[COL_POT].sum() / 1e3:,.0f} MW "
          f"({cand[COL_POT].sum() / df[COL_POT].sum():.2%} da potência)")
    print("tamanho dos grupos:",
          d.groupby("dup_grupo").size().value_counts().sort_index().head(6).to_dict())

    se = cand[cand["SigUF"].isin(UFS_SE)]
    tot_se = df[df["SigUF"].isin(UFS_SE)].groupby("SigUF")[COL_POT].sum()
    print("\nSudeste, cópias por UF:")
    print(se.groupby("SigUF").agg(usinas=(COL_POT, "size"), MW=(COL_POT, lambda s: s.sum() / 1e3))
          .assign(pct_potencia_uf=lambda x: x["MW"] * 1e3 / tot_se * 100).round(2).to_string())
    print("\nSudeste, MW de cópias por ano da data da usina:")
    print((se.groupby([se[COL_DATA].dt.year, "SigUF"])[COL_POT].sum().unstack() / 1e3)
          .loc[2019:].round(1).to_string())

    emit = estima_emissao(df).loc[cand.index]
    lotes = emit.astype(str).value_counts()
    print("\nmês estimado de emissão do código das cópias (5 maiores):")
    print(lotes.head(5).to_string())
    print(f"fração das cópias nesses 2 maiores lotes: {lotes.head(2).sum() / len(cand):.1%}")

    if tecnico is not None and tecnico.exists():
        t = pd.read_parquet(tecnico, columns=[
            "CodGeracaoDistribuida", "NomFabricanteModulo", "NomFabricanteInversor",
            "QtdModulos", "MdaAreaArranjo"]).set_index("CodGeracaoDistribuida")
        x = d[["dup_grupo", COL_COD]].join(t, on=COL_COD)
        for c in ["NomFabricanteModulo", "NomFabricanteInversor"]:
            x[c] = x[c].fillna("").str.upper().str.replace(r"[^A-Z0-9]", "", regex=True).str[:6]
        igual = x.groupby("dup_grupo")[list(t.columns)].nunique(dropna=False).eq(1)
        print("\ngrupos com dados técnicos iguais (fabricantes normalizados):",
              igual.mean().round(3).to_dict(), f"| todos: {igual.all(axis=1).mean():.1%}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--input", type=Path, default=ROOT / "data" / "raw" / "aneel"
                   / "empreendimento-geracao-distribuida.parquet")
    p.add_argument("--tecnico", type=Path, default=ROOT / "data" / "raw" / "aneel"
                   / "empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet")
    p.add_argument("--output", type=Path, default=ROOT / "data" / "interim" / "aneel_gd_preproc.parquet")
    args = p.parse_args(argv)
    logging.basicConfig(force=True, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 200)

    df = load(args.input)
    log.info("lidas %d usinas (%.2f GW)", len(df), df[COL_POT].sum() / 1e6)
    df = remove_sentinela(df)
    df = flag_duplicatas(df)
    resumo_duplicatas(df, args.tecnico)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.output, index=False)
    log.info("salvo %s (%d usinas; colunas novas: cod_num, dup_grupo, dup_n, dup_candidato)",
             args.output, len(df))


if __name__ == "__main__":
    main()
