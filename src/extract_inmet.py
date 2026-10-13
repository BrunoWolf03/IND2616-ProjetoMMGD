"""Extração dos dados horários das estações automáticas do INMET (Sudeste).

Fonte: https://portal.inmet.gov.br/uploads/dadoshistoricos/{ANO}.zip

Comportamentos observados nos arquivos reais de 2023-2026 (out/2026):
- O servidor derruba conexões com o User-Agent padrão do curl; com UA de
  navegador responde normalmente.
- Um CSV por estação e ano, nome INMET_{REG}_{UF}_{CODIGO}_{NOME}_{dd-mm-aaaa}_A_{dd-mm-aaaa}.CSV.
  Em 2025 os arquivos ficam numa subpasta "2025/".
- O zip de 2026 traz DOIS cortes por estação (até 31/08 e até 30/09). O mais
  recente preenche lacunas do anterior, então fica o de data final maior.
- latin-1, separador ";", vírgula decimal (às vezes sem zero: ",1"), ";" final.
- 8 linhas de metadados (REGIAO, UF, ESTACAO, CODIGO (WMO), LATITUDE, LONGITUDE,
  ALTITUDE, DATA DE FUNDACAO) e depois o cabeçalho da tabela.
- Em 2023-2026: colunas "Data" (aaaa/mm/dd) e "Hora UTC" ("1200 UTC"). Anos
  anteriores usam "DATA (YYYY-MM-DD)" / "HORA (UTC)" ("12:00"); ambos são aceitos.
- Faltantes vêm vazios (nenhum -9999 no Sudeste 2023-2026, mas é tratado).
- Radiação global em kJ/m², vazia à noite.
- Convenção do horário: o rótulo é o FIM da hora (15:00 UTC = 14:00-15:00),
  igual à série horária do ONS. Ver check_convencao_horario().
"""

from __future__ import annotations

import argparse
import io
import logging
import random
import re
import time
import unicodedata
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

URL = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{ano}.zip"
UFS_SE = ["SP", "MG", "RJ", "ES"]
ANOS = [2023, 2024, 2025, 2026]

# prefixo normalizado (sem acento, maiúsculo) da coluna no CSV -> nome de saída
VARS = {
    "RADIACAO GLOBAL": "radiacao_kjm2",
    "TEMPERATURA DO AR - BULBO SECO, HORARIA": "temperatura_c",
    "UMIDADE RELATIVA DO AR, HORARIA": "umidade_pct",
    "PRECIPITACAO TOTAL, HORARIO": "precipitacao_mm",
    "VENTO, VELOCIDADE HORARIA": "vento_ms",
}
VAR_COLS = list(VARS.values())

LIMIAR_FALHA = 0.20  # fração máxima de faltantes (radiação diurna) por estação-ano
TOA_DIA_WM2 = 100  # hora "diurna" para a checagem de falhas: TOA média >= 100 W/m²
MAX_RETRIES = 5

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("extract_inmet")

FNAME_RE = re.compile(
    r"INMET_(?P<reg>[A-Z]+)_(?P<uf>[A-Z]{2})_(?P<codigo>[A-Z]\d{3})_(?P<nome>.+?)_"
    r"(?P<ini>\d{2}-\d{2}-\d{4})_A_(?P<fim>\d{2}-\d{2}-\d{4})\.CSV$",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


# --------------------------------------------------------------------------- download
def download_zip(ano: int, raw_dir: Path) -> Path:
    """Baixa {ano}.zip para raw_dir (cache: não baixa se já existe), com retry."""
    path = raw_dir / f"{ano}.zip"
    if path.exists():
        return path
    raw_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".zip.part")
    headers = {"User-Agent": "Mozilla/5.0 (IND2616-ProjetoMMGD)"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with requests.get(URL.format(ano=ano), headers=headers, stream=True, timeout=120) as r:
                r.raise_for_status()
                expected = int(r.headers.get("Content-Length", 0))
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            if expected and tmp.stat().st_size != expected:
                raise IOError(f"tamanho {tmp.stat().st_size} != {expected}")
            zipfile.ZipFile(tmp).testzip()
            tmp.rename(path)
            log.info("baixado %s (%.0f MB)", path, path.stat().st_size / 1e6)
            return path
        except (requests.RequestException, IOError, zipfile.BadZipFile) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2**attempt + random.uniform(0, 1)
            log.warning("%s tentativa %d falhou (%s); nova tentativa em %.1fs", ano, attempt, e, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def extract_ufs(zip_path: Path, ano: int, ufs: list[str], raw_dir: Path) -> list[Path]:
    """Extrai só os CSVs das UFs pedidas para raw_dir/{ano}/ (nome sem subpasta).

    Se houver mais de um arquivo por estação (zip de 2026), fica o de data final maior.
    """
    out_dir = raw_dir / str(ano)
    out_dir.mkdir(parents=True, exist_ok=True)
    escolhidos: dict[str, tuple[pd.Timestamp, zipfile.ZipInfo]] = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            m = FNAME_RE.search(Path(info.filename).name)
            if not m or m["uf"].upper() not in ufs:
                continue
            fim = pd.to_datetime(m["fim"], format="%d-%m-%Y")
            cod = m["codigo"].upper()
            if cod not in escolhidos or fim > escolhidos[cod][0]:
                escolhidos[cod] = (fim, info)
        paths = []
        for _, info in escolhidos.values():
            dest = out_dir / Path(info.filename).name
            if not dest.exists():
                dest.write_bytes(z.read(info))
            paths.append(dest)
    log.info("%d: %d estações extraídas em %s", ano, len(paths), out_dir)
    return sorted(paths)


# --------------------------------------------------------------------------- leitura
def read_metadata(path: Path) -> dict:
    """Lê as 8 linhas de metadados do cabeçalho."""
    with open(path, encoding="latin-1") as f:
        lines = [next(f).rstrip("\n") for _ in range(8)]
    meta = {}
    for line in lines:
        k, _, v = line.partition(";")
        meta[_norm(k).rstrip(":")] = v.split(";")[0].strip()

    def num(v: str) -> float:
        return float(v.replace(",", ".")) if v else np.nan

    return {
        "codigo": meta["CODIGO (WMO)"],
        "nome": meta["ESTACAO"],
        "uf": meta["UF"],
        "regiao": meta["REGIAO"],
        "lat": num(meta["LATITUDE"]),
        "lon": num(meta["LONGITUDE"]),
        "altitude": num(meta["ALTITUDE"]),
        "data_fundacao": meta["DATA DE FUNDACAO"],
    }


def _parse_datetime(data: pd.Series, hora: pd.Series) -> pd.Series:
    """Aceita 'aaaa/mm/dd' ou 'aaaa-mm-dd' e '1200 UTC' ou '12:00'."""
    d = pd.to_datetime(data.str.replace("/", "-", regex=False), format="%Y-%m-%d")
    hhmm = hora.str.replace(r"\D", "", regex=True).str.zfill(4)
    h = pd.to_timedelta(hhmm.str[:2].astype(int), unit="h") + pd.to_timedelta(
        hhmm.str[2:4].astype(int), unit="min")
    return (d + h).dt.tz_localize("UTC")


def read_station(path: Path) -> tuple[dict, pd.DataFrame]:
    """Lê um CSV de estação: metadados + tabela com colunas normalizadas."""
    meta = read_metadata(path)
    df = pd.read_csv(path, sep=";", decimal=",", skiprows=8, encoding="latin-1",
                     dtype=str, keep_default_na=False)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    cols = {_norm(c): c for c in df.columns}
    col_data, col_hora = df.columns[0], df.columns[1]
    if not (_norm(col_data).startswith("DATA") and _norm(col_hora).startswith("HORA")):
        raise ValueError(f"{path.name}: colunas de data/hora inesperadas: {col_data!r}, {col_hora!r}")

    out = pd.DataFrame({"datetime_utc": _parse_datetime(df[col_data], df[col_hora])})
    for prefix, name in VARS.items():
        match = [orig for n, orig in cols.items() if n.startswith(prefix)]
        if len(match) != 1:
            raise ValueError(f"{path.name}: coluna {prefix!r} -> {match}")
        v = pd.to_numeric(df[match[0]].str.replace(",", ".", regex=False).replace("", np.nan),
                          errors="coerce")
        out[name] = v.mask(v <= -9999)
    out.insert(1, "codigo_estacao", meta["codigo"])
    out.insert(2, "uf", meta["uf"])
    return meta, out


# --------------------------------------------------------------------------- solar
def toa_wm2(lat: np.ndarray, lon: np.ndarray, t: pd.DatetimeIndex) -> np.ndarray:
    """Irradiância extraterrestre no plano horizontal (W/m²), fórmulas NOAA/Spencer."""
    doy = t.dayofyear.to_numpy()
    hr = (t.hour + t.minute / 60).to_numpy()
    g = 2 * np.pi / 365 * (doy - 1 + (hr - 12) / 24)
    eot = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                    - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    dec = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
           + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    ha = np.radians((hr * 60 + eot + 4 * lon) / 4 - 180)
    la = np.radians(lat)
    cz = np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ha)
    return 1361 * np.clip(cz, 0, None)


def toa_hora(lat, lon, t: pd.Series, centro_min: float = -30) -> np.ndarray:
    """TOA média na hora cujo centro está `centro_min` minutos do rótulo
    (-30 = rótulo no fim da hora). Integra em passos de 5 min."""
    t = pd.DatetimeIndex(t)
    passos = range(-30 + 2, 30, 5)  # centros dos 12 sub-intervalos de 5 min
    return sum(toa_wm2(lat, lon, t + pd.Timedelta(minutes=centro_min + m)) for m in passos) / 12


# --------------------------------------------------------------------------- montagem
def build(ufs: list[str], anos: list[int], raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    metas, frames = [], []
    for ano in anos:
        zp = download_zip(ano, raw_dir)
        for p in extract_ufs(zp, ano, ufs, raw_dir):
            meta, df = read_station(p)
            metas.append({**meta, "ano": ano, "arquivo": p.name})
            frames.append(df)
    meta = pd.DataFrame(metas)
    dados = pd.concat(frames, ignore_index=True)

    n = len(dados)
    dados = dados.drop_duplicates(["codigo_estacao", "datetime_utc"], keep="last")
    if len(dados) < n:
        log.warning("%d linhas duplicadas removidas", n - len(dados))
    return meta, dados.sort_values(["codigo_estacao", "datetime_utc"]).reset_index(drop=True)


def estacoes_table(meta: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por estação (metadados do ano mais recente) + anos disponíveis."""
    # entre anos há ajustes de ~1e-4° nas coordenadas; só avisa mudanças relevantes
    amp = meta.groupby("codigo")[["lat", "lon", "altitude"]].agg(lambda s: s.max() - s.min())
    mudou = amp[(amp[["lat", "lon"]] > 0.01).any(axis=1) | (amp["altitude"] > 10)]
    if len(mudou):
        log.warning("metadados mudaram entre anos:\n%s", mudou.round(3).to_string())
    est = meta.sort_values("ano").groupby("codigo").last()
    est["anos"] = meta.groupby("codigo")["ano"].apply(lambda s: ",".join(map(str, sorted(s))))
    return est.reset_index()[["codigo", "nome", "uf", "lat", "lon", "altitude",
                              "data_fundacao", "anos"]]


def tratar_radiacao(dados: pd.DataFrame, est: pd.DataFrame) -> pd.DataFrame:
    """Radiação noturna (NaN ou negativa com o sol abaixo do horizonte a hora toda) -> 0.
    Radiação faltante de dia continua NaN. Acrescenta Wh/m² e a TOA da hora."""
    pos = est.set_index("codigo")[["lat", "lon"]].reindex(dados["codigo_estacao"])
    toa = toa_hora(pos["lat"].to_numpy(), pos["lon"].to_numpy(), dados["datetime_utc"])
    noite = toa == 0
    r = dados["radiacao_kjm2"]
    dados = dados.assign(
        radiacao_kjm2=r.mask(noite & (r.isna() | (r < 0)), 0.0),
        toa_wm2=toa,
    )
    dados["radiacao_whm2"] = dados["radiacao_kjm2"] / 3.6
    return dados


# --------------------------------------------------------------------------- checagens
def check_faltantes(dados: pd.DataFrame) -> pd.DataFrame:
    """% de faltantes por estação e ano: radiação nas horas diurnas, demais em todas."""
    d = dados.assign(ano=dados["datetime_utc"].dt.year, dia=dados["toa_wm2"] >= TOA_DIA_WM2)
    g = d.groupby(["codigo_estacao", "uf", "ano"])
    out = g.size().rename("n_horas").to_frame()
    out["n_horas_dia"] = g["dia"].sum()
    out["falta_rad_dia_pct"] = (
        d[d["dia"]].groupby(["codigo_estacao", "uf", "ano"])["radiacao_kjm2"]
        .apply(lambda s: s.isna().mean() * 100))
    for c in ["temperatura_c", "umidade_pct", "precipitacao_mm", "vento_ms"]:
        out[f"falta_{c.split('_')[0]}_pct"] = g[c].apply(lambda s: s.isna().mean() * 100)
    out["passa_rad"] = out["falta_rad_dia_pct"] <= LIMIAR_FALHA * 100
    return out.reset_index()


def check_convencao_horario(dados: pd.DataFrame, est: pd.DataFrame) -> pd.DataFrame:
    """Para cada estação, correlação da radiação com a TOA integrada supondo rótulo no
    fim da hora (centro -30 min) vs. no início (centro +30 min)."""
    pos = est.set_index("codigo")[["lat", "lon"]]
    rows = []
    for cod, d in dados.groupby("codigo_estacao"):
        d = d[d["radiacao_kjm2"].notna()]
        if len(d) < 1000:
            continue
        lat, lon = pos.loc[cod]
        r = {"codigo_estacao": cod}
        for nome, c in [("fim", -30), ("inicio", 30)]:
            r[f"corr_{nome}"] = np.corrcoef(d["radiacao_kjm2"], toa_hora(lat, lon, d["datetime_utc"], c))[0, 1]
        rows.append(r)
    out = pd.DataFrame(rows)
    out["melhor"] = np.where(out["corr_fim"] >= out["corr_inicio"], "fim", "inicio")
    return out


def plot_mapa(est: pd.DataFrame, qual: pd.DataFrame, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ok = qual.groupby("codigo_estacao")["passa_rad"].mean().rename("frac_ok")
    e = est.merge(ok, left_on="codigo", right_index=True, how="left")
    fig, ax = plt.subplots(figsize=(7, 6))
    try:
        import geobr
        geobr.read_state(year=2020).query("abbrev_state in @UFS_SE").boundary.plot(
            ax=ax, color="grey", lw=0.8)
    except Exception as exc:  # sem internet ou geobr indisponível: só os pontos
        log.warning("mapa sem contornos (%s)", exc)
    cores = {"SP": "#1f77b4", "MG": "#ff7f0e", "RJ": "#2ca02c", "ES": "#d62728"}
    for uf, g in e.groupby("uf"):
        boa = g["frac_ok"] == 1
        ax.scatter(g.loc[boa, "lon"], g.loc[boa, "lat"], s=22, color=cores.get(uf), label=f"{uf} ({len(g)})")
        ax.scatter(g.loc[~boa, "lon"], g.loc[~boa, "lat"], s=40, marker="x", color=cores.get(uf))
    ax.set_title("Estações automáticas INMET — Sudeste\n(x = reprovada em ao menos um ano: >20% de falhas de radiação diurna)", fontsize=10)
    ax.set_xlim(e["lon"].min() - 1, e["lon"].max() + 1)
    ax.set_ylim(e["lat"].min() - 1, e["lat"].max() + 1)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_perfil(por_uf: pd.DataFrame, meses: list[str], out_path: Path) -> None:
    """Radiação média por hora UTC (rótulo = fim da hora) por UF, para cada mês dado."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(meses), figsize=(5 * len(meses), 4), sharey=True)
    for ax, mes in zip(np.atleast_1d(axes), meses):
        d = por_uf[por_uf["datetime_utc"].dt.strftime("%Y-%m") == mes]
        perfil = d.groupby([d["datetime_utc"].dt.hour, "uf"])["radiacao_whm2"].mean().unstack()
        perfil.plot(ax=ax, marker="o", ms=3)
        ax.axvline(15, color="grey", ls=":", lw=1)
        ax.set_title(mes)
        ax.set_xlabel("hora UTC (rótulo = fim da hora)")
        ax.set_xticks(range(0, 24, 3))
        ax.grid(alpha=0.3)
    np.atleast_1d(axes)[0].set_ylabel("radiação média (Wh/m² na hora)")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- agregação
def agrega_por_uf(dados: pd.DataFrame, qual: pd.DataFrame) -> pd.DataFrame:
    """Média por UF e hora, só com estação-anos aprovados no filtro de radiação diurna.

    n_estacoes = estações aprovadas com radiação válida naquela hora;
    n_<variavel> = estações com valor válido de cada variável.
    """
    aprovadas = qual.loc[qual["passa_rad"], ["codigo_estacao", "ano"]]
    d = dados.assign(ano=dados["datetime_utc"].dt.year).merge(aprovadas, on=["codigo_estacao", "ano"])
    cols = VAR_COLS + ["radiacao_whm2"]
    g = d.groupby(["uf", "datetime_utc"])
    out = g[cols].mean()
    for c in VAR_COLS:
        out[f"n_{c.split('_')[0]}"] = g[c].count()
    out["n_estacoes"] = out["n_radiacao"]
    return out.reset_index()


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--anos", nargs="+", type=int, default=ANOS)
    p.add_argument("--ufs", nargs="+", default=UFS_SE)
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--results-dir", type=Path, default=ROOT / "resultados")
    p.add_argument("--meses-plot", nargs="+", default=["2025-01", "2025-07"],
                   help="meses (aaaa-mm) do gráfico de perfil horário")
    args = p.parse_args(argv)

    ufs = [u.upper() for u in args.ufs]
    raw_dir = args.data_dir / "raw" / "inmet"
    interim, processed = args.data_dir / "interim", args.data_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    interim.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(force=True,
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(raw_dir / "extract.log")])
    pd.set_option("display.width", 200)

    meta, dados = build(ufs, args.anos, raw_dir)
    est = estacoes_table(meta)
    est.to_csv(interim / "inmet_estacoes_sudeste.csv", index=False)
    dados = tratar_radiacao(dados, est)
    dados.drop(columns="toa_wm2").to_parquet(interim / "inmet_horario_sudeste.parquet", index=False)
    log.info("salvo inmet_horario_sudeste.parquet (%d linhas, %d estações)",
             len(dados), dados["codigo_estacao"].nunique())

    # checagens
    checks = interim / "checks_inmet"
    checks.mkdir(exist_ok=True)
    qual = check_faltantes(dados)
    qual.to_csv(checks / "faltantes_estacao_ano.csv", index=False)
    print("\n=== Estações por UF ===")
    print(est.groupby("uf").agg(n_estacoes=("codigo", "size"),
                                altitude_media=("altitude", "mean")).round(0).to_string())
    print("\n=== Faltantes (%) por UF e ano: mediana entre estações ===")
    print(qual.groupby(["uf", "ano"])[[c for c in qual if c.startswith("falta_")]]
          .median().round(1).to_string())
    ruins = qual[~qual["passa_rad"]].merge(est[["codigo", "nome"]], left_on="codigo_estacao",
                                           right_on="codigo")
    print(f"\n=== Estação-anos com >{LIMIAR_FALHA:.0%} de falhas de radiação diurna "
          f"({len(ruins)} de {len(qual)}) ===")
    print(ruins[["codigo_estacao", "nome", "uf", "ano", "falta_rad_dia_pct", "falta_temperatura_pct"]]
          .sort_values(["uf", "codigo_estacao", "ano"]).round(1).to_string(index=False))

    conv = check_convencao_horario(dados, est)
    conv.to_csv(checks / "convencao_horario.csv", index=False)
    print("\n=== Convenção do horário (correlação com TOA): estações por melhor ajuste ===")
    print(conv["melhor"].value_counts().to_string(),
          f"\ncorr média: fim={conv['corr_fim'].mean():.3f} início={conv['corr_inicio'].mean():.3f}")

    por_uf = agrega_por_uf(dados, qual)
    por_uf.to_parquet(processed / "inmet_horario_por_uf.parquet", index=False)
    log.info("salvo inmet_horario_por_uf.parquet (%d linhas)", len(por_uf))
    print("\n=== n_estacoes por UF (média por hora diurna) ===")
    dia = por_uf[por_uf["radiacao_kjm2"] > 0]
    print(dia.groupby("uf")["n_estacoes"].describe()[["mean", "min", "max"]].round(1).to_string())

    pico = (por_uf[por_uf["datetime_utc"].dt.month.isin([12, 1, 2])]
            .groupby(["uf", por_uf["datetime_utc"].dt.hour])["radiacao_whm2"].mean()
            .groupby("uf").idxmax().map(lambda t: t[1]))
    print("\n=== Hora (rótulo UTC) do pico médio de radiação no verão ===")
    print(pico.to_string())

    plot_mapa(est, qual, args.results_dir / "inmet_estacoes_sudeste.png")
    plot_perfil(por_uf, args.meses_plot, args.results_dir / "inmet_perfil_radiacao_uf.png")


if __name__ == "__main__":
    main()
