"""Download das bases de MMGD da ANEEL (dados abertos, CKAN).

Dataset "Relação de empreendimentos de Geração Distribuída":
https://dadosabertos.aneel.gov.br/dataset/5e0fafd2-21b9-4d5b-b622-40438d40aba2

Arquivos baixados para data/raw/aneel/:
- empreendimento-geracao-distribuida.parquet: base principal (uma linha por usina).
- empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet: dados técnicos das
  UFV (DatConexao, módulos, inversores); liga com a principal por
  CodGeracaoDistribuida = CodEmpreendimento.

As URLs são descobertas pela API do CKAN (package_show), procurando o recurso
pelo nome do arquivo, então sobrevivem a troca de ID dos recursos. Se a API não
responder, usa as URLs conhecidas em FALLBACK_URLS.

Observações:
- Cada arquivo é um RETRATO do cadastro (AnmPeriodoReferencia constante), não há
  histórico. Use --refresh para trocar pelo retrato mais recente.
- O servidor às vezes derruba o handshake TLS por alguns minutos; o retry com
  backoff cobre quedas curtas.
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from pathlib import Path

import pyarrow.parquet as pq
import requests

PACKAGE_ID = "5e0fafd2-21b9-4d5b-b622-40438d40aba2"
CKAN_API = "https://dadosabertos.aneel.gov.br/api/3/action/package_show"
ARQUIVOS = [
    "empreendimento-geracao-distribuida.parquet",
    "empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet",
]
FALLBACK_URLS = {
    "empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet":
        f"https://dadosabertos.aneel.gov.br/dataset/{PACKAGE_ID}/resource/"
        "703c4cb8-b7e2-4f27-a9bb-7e55324a88a4/download/"
        "empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (IND2616-ProjetoMMGD)"}
MAX_RETRIES = 5

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("extract_aneel")


def _get(url: str, **kw) -> requests.Response:
    """GET com retry e backoff exponencial (cobre as quedas de TLS do servidor)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=120, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2**attempt + random.uniform(0, 1)
            log.warning("%s: tentativa %d falhou (%s); nova tentativa em %.1fs",
                        url.split("/")[-1][:60], attempt, e, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def resolve_urls(arquivos: list[str]) -> dict[str, str]:
    """Nome do arquivo -> URL de download, via CKAN; cai no fallback se falhar."""
    urls = {}
    try:
        res = _get(CKAN_API, params={"id": PACKAGE_ID}).json()["result"]["resources"]
        for r in res:
            nome = r["url"].rsplit("/", 1)[-1]
            if nome in arquivos:
                urls[nome] = r["url"]
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("API do CKAN indisponível (%s); usando URLs conhecidas", e)
    for nome in arquivos:
        if nome not in urls and nome in FALLBACK_URLS:
            urls[nome] = FALLBACK_URLS[nome]
    faltando = set(arquivos) - set(urls)
    if faltando:
        raise RuntimeError(f"sem URL para {sorted(faltando)} (CKAN fora do ar e sem fallback)")
    return urls


def download(url: str, dest: Path) -> None:
    """Baixa para .part, valida o parquet e só então renomeia."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _get(url, stream=True) as r:
        esperado = int(r.headers.get("Content-Length", 0))
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    if esperado and tmp.stat().st_size != esperado:
        tmp.unlink()
        raise IOError(f"{dest.name}: download incompleto ({tmp.stat().st_size} de {esperado} bytes)")
    pq.ParquetFile(tmp)  # levanta erro se não for um parquet válido
    tmp.replace(dest)


def resumo(path: Path) -> str:
    f = pq.ParquetFile(path)
    data = f.read_row_group(0, columns=["DatGeracaoConjuntoDados"]).column(0)[0]
    return f"{f.metadata.num_rows:,} linhas, retrato de {data}"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--refresh", action="store_true",
                   help="baixa de novo mesmo se já existe (pega o retrato mais recente)")
    args = p.parse_args(argv)
    logging.basicConfig(force=True, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    raw_dir = args.data_dir / "raw" / "aneel"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pendentes = [a for a in ARQUIVOS if args.refresh or not (raw_dir / a).exists()]
    if pendentes:
        for nome, url in resolve_urls(pendentes).items():
            log.info("baixando %s", nome)
            download(url, raw_dir / nome)
    for nome in ARQUIVOS:
        log.info("%s: %s", nome, resumo(raw_dir / nome))


if __name__ == "__main__":
    main()
