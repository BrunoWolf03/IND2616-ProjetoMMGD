"""Extração da carga verificada semi-horária do ONS (API apicarga).

Endpoint: https://apicarga.ons.org.br/prd/cargaverificada
    ?dat_inicio=YYYY-MM-DD&dat_fim=YYYY-MM-DD&cod_areacarga=XX

Comportamentos da API observados em testes (out/2026), que orientam este script:
- Resposta é uma lista JSON de registros; campos: cod_areacarga, din_atualizacao,
  dat_referencia, din_referenciautc, val_cargaglobal, val_cargaglobalcons,
  val_cargaglobalsmmgd, val_cargasupervisionada, val_carganaosupervisionada,
  val_cargammgd, val_consistencia.
- dat_referencia é o dia em horário de Brasília; din_referenciautc é o FIM do
  intervalo de 30 min em UTC (dia local = 00:30 até 24:00 BRT, 48 registros).
- Valores nulos vêm como JSON inválido (`"campo": ,`) -> parser tolerante.
- Requisições longas são TRUNCADAS em silêncio (HTTP 200, ~4944 registros).
  Blocos mensais (<= 1488 registros) ficam bem abaixo do limite.
- O dia corrente vem parcial, com val_cargaglobal = 0 nos intervalos futuros.
- val_cargammgd é nula antes de 2019-02-17.
- O código "SE" na API é Sergipe, por isso a soma SP+MG+RJ+ES se chama SUDESTE.
- val_consistencia é um ajuste em MW: val_cargaglobalcons = val_cargaglobal +
  val_consistencia. A carga bruta tem picos espúrios (ex.: ES 18.676 MW em
  2024-02-08) que a consistida corrige. A identidade
  val_cargaglobalsmmgd = val_cargaglobal - val_cargammgd vale com a carga BRUTA,
  então a carga líquida é calculada aqui como val_cargaglobalcons - val_cargammgd.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API_URL = "https://apicarga.ons.org.br/prd/cargaverificada"
TZ_LOCAL = ZoneInfo("America/Sao_Paulo")

AREAS_SE = ["SP", "MG", "RJ", "ES"]
AREAS_CHECK = ["SECO", "DF", "GO", "MT", "MS", "PESE"]
AREAS_SECO_STATES = AREAS_SE + ["DF", "GO", "MT", "MS"]
AREA_SUDESTE = "SUDESTE"  # SP+MG+RJ+ES ("SE" já é Sergipe na API)

VAL_COLS = [
    "val_cargaglobal",
    "val_cargaglobalcons",
    "val_cargaglobalsmmgd",
    "val_cargasupervisionada",
    "val_carganaosupervisionada",
    "val_cargammgd",
    "val_consistencia",
]

MAX_RETRIES = 5
PAUSE_S = 0.5
TIMEOUT_S = 120

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("extract_ons")


# --------------------------------------------------------------------------- fetch
def parse_response(text: str) -> list[dict]:
    """Converte a resposta em lista de dicts, tratando `"campo": ,` como null."""
    fixed = re.sub(r":\s*(?=[,}\]])", ": null", text)
    data = json.loads(fixed)
    if not isinstance(data, list):
        raise ValueError(f"Resposta inesperada (não é lista): {str(data)[:200]}")
    return data


def fetch_block(
    session: requests.Session, area: str, start: date, end: date
) -> pd.DataFrame:
    """Baixa um bloco [start, end] (datas locais, inclusivas) com retry e backoff."""
    params = {
        "dat_inicio": start.isoformat(),
        "dat_fim": end.isoformat(),
        "cod_areacarga": area,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(API_URL, params=params, timeout=TIMEOUT_S)
            r.raise_for_status()
            return pd.DataFrame(parse_response(r.text))
        except (requests.RequestException, ValueError) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2**attempt + random.uniform(0, 1)
            log.warning(
                "%s %s..%s tentativa %d falhou (%s); nova tentativa em %.1fs",
                area, start, end, attempt, e, wait,
            )
            time.sleep(wait)
    raise AssertionError("unreachable")


def month_blocks(start: date, end: date) -> list[tuple[date, date]]:
    """Divide [start, end] em blocos por mês-calendário."""
    blocks = []
    cur = start
    while cur <= end:
        next_month = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        blocks.append((cur, min(end, next_month - timedelta(days=1))))
        cur = next_month
    return blocks


# --------------------------------------------------------------------------- cache
def cache_path(raw_dir: Path, area: str, month: date) -> Path:
    return raw_dir / area / f"{month:%Y-%m}.parquet"


def cache_covers(path: Path, start: date, end: date) -> bool:
    """O arquivo em cache existe e cobre [start, end]? (meses parciais são rebaixados)."""
    if not path.exists():
        return False
    d = pd.read_parquet(path, columns=["dat_referencia"])["dat_referencia"]
    return len(d) > 0 and d.min() <= start.isoformat() and d.max() >= end.isoformat()


def download(
    areas: list[str], start: date, end: date, raw_dir: Path, refresh: bool = False
) -> pd.DataFrame:
    """Baixa os blocos que faltam no cache. Retorna relatório de problemas por bloco."""
    problems = []
    session = requests.Session()
    session.headers["User-Agent"] = "IND2616-ProjetoMMGD/1.0"
    for area in areas:
        for b_start, b_end in month_blocks(start, end):
            path = cache_path(raw_dir, area, b_start)
            if not refresh and cache_covers(path, b_start, b_end):
                continue
            try:
                df = fetch_block(session, area, b_start, b_end)
            except Exception as e:
                log.error("FALHA %s %s: %s", area, f"{b_start:%Y-%m}", e)
                problems.append((area, f"{b_start:%Y-%m}", "falha", str(e)[:300]))
                continue
            finally:
                time.sleep(PAUSE_S)

            if df.empty:
                log.warning("VAZIO %s %s", area, f"{b_start:%Y-%m}")
                problems.append((area, f"{b_start:%Y-%m}", "vazio", ""))
                continue
            # Truncamento silencioso da API ou dados ainda não publicados.
            if df["dat_referencia"].max() < b_end.isoformat():
                msg = f"último dia {df['dat_referencia'].max()} < {b_end}"
                log.warning("INCOMPLETO %s %s: %s", area, f"{b_start:%Y-%m}", msg)
                problems.append((area, f"{b_start:%Y-%m}", "incompleto", msg))

            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=False)
            log.info("ok %s %s (%d registros)", area, f"{b_start:%Y-%m}", len(df))

    report = pd.DataFrame(problems, columns=["area", "mes", "status", "detalhe"])
    if not report.empty:
        out = raw_dir / f"problemas_{datetime.now():%Y%m%d_%H%M%S}.csv"
        report.to_csv(out, index=False)
        log.warning("%d blocos com problema -> %s", len(report), out)
    return report


def load_cache(areas: list[str], start: date, end: date, raw_dir: Path) -> pd.DataFrame:
    """Concatena os blocos brutos do cache (sem deduplicar)."""
    frames = [
        pd.read_parquet(p)
        for area in areas
        for b_start, _ in month_blocks(start, end)
        if (p := cache_path(raw_dir, area, b_start)).exists()
    ]
    if not frames:
        raise FileNotFoundError(f"Nenhum bloco em cache em {raw_dir}")
    raw = pd.concat(frames, ignore_index=True)
    in_range = raw["dat_referencia"].between(start.isoformat(), end.isoformat())
    return raw[in_range].reset_index(drop=True)


# --------------------------------------------------------------------------- consolidate
def consolidate(raw: pd.DataFrame, out_path: Path) -> pd.DataFrame:
    """Tipa as colunas, remove duplicatas (mantém a atualização mais recente) e salva."""
    df = raw.copy()
    df["din_referenciautc"] = pd.to_datetime(df["din_referenciautc"], utc=True)
    df["din_atualizacao"] = pd.to_datetime(df["din_atualizacao"], utc=True)
    df["dat_referencia"] = pd.to_datetime(df["dat_referencia"]).dt.date
    df[VAL_COLS] = df[VAL_COLS].astype("float64")
    df["val_cargaliquida"] = df["val_cargaglobalcons"] - df["val_cargammgd"]

    n = len(df)
    df = (
        df.sort_values("din_atualizacao")
        .drop_duplicates(["cod_areacarga", "din_referenciautc"], keep="last")
        .sort_values(["cod_areacarga", "din_referenciautc"])
        .reset_index(drop=True)
    )
    if len(df) < n:
        log.warning("consolidate: %d duplicatas removidas", n - len(df))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info("salvo %s (%d registros)", out_path, len(df))
    return df


# --------------------------------------------------------------------------- hourly
def to_hourly(semi: pd.DataFrame, out_path: Path) -> pd.DataFrame:
    """Semi-hora -> hora (média), rótulo no fim do intervalo: 14:30 e 15:00 -> 15:00 UTC.

    Horas sem os dois registros semi-horários ficam NaN (n_obs indica quantos havia).
    Acrescenta a área SUDESTE = SP+MG+RJ+ES, só nas horas em que os quatro existem.
    """
    df = semi.assign(hora_utc=semi["din_referenciautc"].dt.ceil("h"))
    g = df.groupby(["cod_areacarga", "hora_utc"])
    hourly = g[VAL_COLS].mean()
    hourly["n_obs"] = g.size()
    hourly.loc[hourly["n_obs"] != 2, VAL_COLS] = float("nan")
    hourly = hourly.reset_index()

    se = hourly[hourly["cod_areacarga"].isin(AREAS_SE)]
    se = se[se.groupby("hora_utc")["cod_areacarga"].transform("nunique") == len(AREAS_SE)]
    sudeste = (
        se.groupby("hora_utc")
        .agg({**{c: lambda s: s.sum(min_count=len(AREAS_SE)) for c in VAL_COLS},
              "n_obs": "min"})
        .reset_index()
        .assign(cod_areacarga=AREA_SUDESTE)
    )

    hourly = pd.concat([hourly, sudeste], ignore_index=True)
    hourly["val_cargaliquida"] = hourly["val_cargaglobalcons"] - hourly["val_cargammgd"]
    hourly = hourly.sort_values(["cod_areacarga", "hora_utc"]).reset_index(drop=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    hourly.to_parquet(out_path, index=False)
    log.info("salvo %s (%d registros)", out_path, len(hourly))
    return hourly


# --------------------------------------------------------------------------- checks
def expected_stamps(start: date, end: date) -> pd.DatetimeIndex:
    """Instantes UTC esperados (fim de intervalo) para os dias locais [start, end]."""
    first = pd.Timestamp(datetime.combine(start, datetime.min.time()), tz=TZ_LOCAL)
    last = pd.Timestamp(datetime.combine(end + timedelta(days=1), datetime.min.time()), tz=TZ_LOCAL)
    return pd.date_range(first + pd.Timedelta("30min"), last, freq="30min").tz_convert("UTC")


def check_counts(raw: pd.DataFrame, areas: list[str], start: date, end: date) -> dict:
    """Esperado vs obtido por área/mês, lacunas, duplicatas e cargas globais zeradas."""
    exp = expected_stamps(start, end)
    exp_month = (exp.tz_convert(TZ_LOCAL) - pd.Timedelta("30min")).strftime("%Y-%m")
    ts = pd.to_datetime(raw["din_referenciautc"], utc=True)
    raw = raw.assign(ts=ts, mes=pd.to_datetime(raw["dat_referencia"]).dt.strftime("%Y-%m"))

    counts = (
        raw.groupby(["cod_areacarga", "mes"]).agg(
            obtidos=("ts", "size"), unicos=("ts", "nunique"),
            global_nao_pos=("val_cargaglobal", lambda s: int((s <= 0).sum())),
            consistidos=("val_consistencia", lambda s: int((s.fillna(0) != 0).sum())),
            mmgd_nulo=("val_cargammgd", lambda s: int(s.isna().sum())),
        )
        .reindex(pd.MultiIndex.from_product([areas, sorted(set(exp_month))],
                                            names=["cod_areacarga", "mes"]))
        .fillna(0).astype(int)
    )
    counts.insert(0, "esperados", pd.Series(exp_month).value_counts().reindex(
        counts.index.get_level_values("mes")).to_numpy())
    counts["faltando"] = counts["esperados"] - counts["unicos"]
    counts["duplicatas"] = counts["obtidos"] - counts["unicos"]

    gaps = []
    for area in areas:
        missing = exp.difference(pd.DatetimeIndex(raw.loc[raw["cod_areacarga"] == area, "ts"]))
        if len(missing):
            # agrupa instantes consecutivos em intervalos
            run = (missing.to_series().diff() != pd.Timedelta("30min")).cumsum()
            for _, s in missing.to_series().groupby(run.to_numpy()):
                gaps.append((area, s.iloc[0], s.iloc[-1], len(s)))
    gaps = pd.DataFrame(gaps, columns=["cod_areacarga", "inicio_utc", "fim_utc", "n"])

    dups = raw[raw.duplicated(["cod_areacarga", "ts"], keep=False)].sort_values(
        ["cod_areacarga", "ts"])
    return {"contagens": counts.reset_index(), "lacunas": gaps, "duplicatas": dups}


def check_identity(semi: pd.DataFrame, tol: float = 0.01) -> pd.DataFrame:
    """carga - val_cargammgd == val_cargaglobalsmmgd (tolerância em MW), com a carga
    bruta (val_cargaglobal) e com a consistida (val_cargaglobalcons)."""
    out = []
    for col in ["val_cargaglobal", "val_cargaglobalcons"]:
        diff = semi[col] - semi["val_cargammgd"] - semi["val_cargaglobalsmmgd"]
        out.append(diff.groupby(semi["cod_areacarga"]).agg(
            n="count",
            max_abs=lambda s: s.abs().max(),
            n_fora_tol=lambda s: int((s.abs() > tol).sum()),
        ).reset_index().assign(carga=col))
    return pd.concat(out, ignore_index=True)


def check_seco(semi: pd.DataFrame, col: str = "val_cargaglobalcons") -> pd.DataFrame:
    """Compara SECO com a soma dos estados e com a soma + PESE (média, MW e %)."""
    wide = semi.pivot_table(index="din_referenciautc", columns="cod_areacarga", values=col)
    needed = AREAS_SECO_STATES + ["SECO", "PESE"]
    missing = [a for a in needed if a not in wide]
    if missing:
        raise KeyError(f"check_seco precisa das áreas {missing}")
    wide = wide[needed].dropna()
    soma = wide[AREAS_SECO_STATES].sum(axis=1)
    rows = []
    for nome, ref in [("soma estados", soma), ("soma estados + PESE", soma + wide["PESE"])]:
        diff = wide["SECO"] - ref
        rows.append({
            "carga": col,
            "comparacao": f"SECO - ({nome})",
            "n": len(diff),
            "media_MW": diff.mean(),
            "media_pct_SECO": (diff / wide["SECO"]).mean() * 100,
            "mediana_MW": diff.median(),
            "std_MW": diff.std(),
            "min_MW": diff.min(),
            "max_MW": diff.max(),
        })
    return pd.DataFrame(rows)


def plot_summer_day(hourly: pd.DataFrame, out_path: Path, day: date | None = None) -> dict:
    """Plota carga global, MMGD e carga líquida do SUDESTE num dia ensolarado de verão.

    Sem `day`, escolhe o dia de dez-fev com maior energia diária de MMGD (proxy de
    dia ensolarado; favorece o verão mais recente, pois a capacidade cresce).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    se = hourly[hourly["cod_areacarga"] == AREA_SUDESTE].copy()
    se["dia_local"] = (se["hora_utc"].dt.tz_convert(TZ_LOCAL) - pd.Timedelta("1h")).dt.date
    if day is None:
        summer = se[pd.to_datetime(se["dia_local"]).dt.month.isin([12, 1, 2])]
        daily = summer.groupby("dia_local")["val_cargammgd"].agg(["sum", "count"])
        day = daily[daily["count"] == 24]["sum"].idxmax()
    d = se[se["dia_local"] == day].set_index("hora_utc")
    peak = d["val_cargammgd"].idxmax()

    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = d.index.tz_convert("UTC").tz_localize(None)
    ax.plot(x, d["val_cargaglobalcons"], label="Carga global (consistida)", color="#1f4e79", lw=2)
    ax.plot(x, d["val_cargaliquida"], label="Carga líquida (consistida − MMGD)", color="#c55a11", lw=2)
    ax.plot(x, d["val_cargammgd"], label="MMGD", color="#e0a100", lw=2)
    ax.axvline(peak.tz_localize(None), color="grey", ls=":", lw=1)
    ax.annotate(f"pico MMGD {peak:%H:%M} UTC", (peak.tz_localize(None), d["val_cargammgd"].max()),
                xytext=(5, 5), textcoords="offset points", fontsize=9, color="grey")
    ax.set_title(f"SUDESTE (SP+MG+RJ+ES) — {day:%d/%m/%Y} — horário UTC, rótulo = fim da hora")
    ax.set_ylabel("MWmed")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    ax.grid(alpha=0.3)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"dia": day, "pico_mmgd_utc": peak, "pico_mmgd_MW": d["val_cargammgd"].max()}


def run_checks(
    raw: pd.DataFrame, semi: pd.DataFrame, hourly: pd.DataFrame,
    areas: list[str], start: date, end: date, out_dir: Path, plot_path: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 200)

    c = check_counts(raw, areas, start, end)
    for k, v in c.items():
        v.to_csv(out_dir / f"{k}.csv", index=False)
    probs = c["contagens"].query("faltando != 0 or duplicatas != 0 or global_nao_pos != 0 or mmgd_nulo != 0")
    print("\n=== Contagens esperado vs obtido (só meses com problema) ===")
    print(probs.to_string(index=False) if len(probs) else "Nenhum problema.")
    print(f"Total: esperados={c['contagens']['esperados'].sum()} "
          f"únicos={c['contagens']['unicos'].sum()} "
          f"duplicatas={c['contagens']['duplicatas'].sum()}")
    print("\n=== Lacunas ===")
    print(c["lacunas"].to_string(index=False) if len(c["lacunas"]) else "Nenhuma.")

    ident = check_identity(semi)
    ident.to_csv(out_dir / "identidade_mmgd.csv", index=False)
    print("\n=== carga - val_cargammgd - val_cargaglobalsmmgd ===")
    print(ident.to_string(index=False))

    try:
        seco = pd.concat([check_seco(semi, c) for c in ["val_cargaglobalcons", "val_cargaglobal"]])
        seco.to_csv(out_dir / "seco_vs_estados.csv", index=False)
        print("\n=== SECO vs soma das áreas ===")
        print(seco.round(2).to_string(index=False))
    except KeyError as e:
        print(f"\n(checagem SECO pulada: {e})")

    if AREA_SUDESTE in set(hourly["cod_areacarga"]):
        info = plot_summer_day(hourly, plot_path)
        print(f"\n=== Dia de verão: {info['dia']} — pico MMGD às "
              f"{info['pico_mmgd_utc']:%H:%M} UTC ({info['pico_mmgd_MW']:.0f} MW) -> {plot_path}")


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    yesterday = datetime.now(TZ_LOCAL).date() - timedelta(days=1)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=yesterday,
                   help="último dia local (padrão: ontem; o dia corrente vem parcial)")
    p.add_argument("--areas", nargs="+", default=AREAS_SE,
                   help="padrão: SP MG RJ ES. Para a checagem da SECO, acrescente: "
                        + " ".join(AREAS_CHECK))
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--results-dir", type=Path, default=ROOT / "resultados")
    p.add_argument("--refresh", action="store_true", help="rebaixa blocos já em cache")
    p.add_argument("--no-download", action="store_true", help="só processa o cache")
    p.add_argument("--no-checks", action="store_true")
    args = p.parse_args(argv)

    if args.end > yesterday:
        print(f"--end {args.end} ajustado para {yesterday} (dia corrente é parcial)")
        args.end = yesterday
    areas = [a.upper() for a in args.areas]
    raw_dir = args.data_dir / "raw" / "ons"
    raw_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(force=True,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(raw_dir / "extract.log")],
    )

    if not args.no_download:
        download(areas, args.start, args.end, raw_dir, refresh=args.refresh)
    raw = load_cache(areas, args.start, args.end, raw_dir)
    semi = consolidate(raw, args.data_dir / "interim" / "ons_carga_semihoraria.parquet")
    hourly = to_hourly(semi, args.data_dir / "processed" / "ons_carga_horaria.parquet")

    if not args.no_checks:
        run_checks(raw, semi, hourly, areas, args.start, args.end,
                   out_dir=args.data_dir / "interim" / "checks_ons",
                   plot_path=args.results_dir / "ons_dia_verao_sudeste.png")


if __name__ == "__main__":
    main()
