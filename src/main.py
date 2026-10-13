"""Baixa e prepara todos os dados do projeto.

Etapas, na ordem:
  ons       carga verificada e MMGD do ONS (src/extract_ons.py)
  inmet     estações automáticas do INMET (src/extract_inmet.py)
  aneel     cadastro de MMGD da ANEEL (src/extract_aneel.py)
  preproc   pré-processamento da base da ANEEL (src/preprocess_aneel.py)

Todas usam cache: rodar de novo só baixa o que falta. Uso:
  python src/main.py                    # tudo
  python src/main.py --etapas ons inmet # só algumas
  python src/main.py --refresh          # baixa de novo ONS e ANEEL (retratos novos)
"""

from __future__ import annotations

import argparse
import time

import extract_aneel
import extract_inmet
import extract_ons
import preprocess_aneel

ETAPAS = ["ons", "inmet", "aneel", "preproc"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--etapas", nargs="+", choices=ETAPAS, default=ETAPAS)
    p.add_argument("--refresh", action="store_true",
                   help="ignora o cache de ONS e ANEEL (o INMET se renova apagando o zip)")
    args = p.parse_args()
    refresh = ["--refresh"] if args.refresh else []

    passos = {
        "ons": lambda: extract_ons.main(refresh),
        "inmet": lambda: extract_inmet.main([]),
        "aneel": lambda: extract_aneel.main(refresh),
        "preproc": lambda: preprocess_aneel.main([]),
    }
    tempos = {}
    for etapa in ETAPAS:
        if etapa not in args.etapas:
            continue
        print(f"\n{'=' * 30} {etapa.upper()} {'=' * 30}", flush=True)
        t0 = time.time()
        passos[etapa]()
        tempos[etapa] = time.time() - t0

    print("\n" + "=" * 70)
    for etapa, t in tempos.items():
        print(f"{etapa:8s} ok  ({t / 60:.1f} min)")


if __name__ == "__main__":
    main()
