"""Estilo comum dos gráficos do projeto (resultados/*.png).

Cores por UF validadas para daltonismo (protan/deutan/tritan) e contraste entre
vizinhas; a cor segue a UF em todos os gráficos. O dourado de MG tem pouco
contraste com o fundo branco, então as séries levam rótulo direto ou legenda.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

UFS = ["SP", "MG", "RJ", "ES"]
CORES_UF = {"SP": "#1A7FA8", "MG": "#D9A02B", "RJ": "#C2553A", "ES": "#6B5BB0"}
TINTA = "#23303A"
TINTA_SUAVE = "#5B6670"
GRADE = "#E6E8EA"
NEUTRO = "#A3A8AC"
DESTAQUE = "#F3E6C4"  # faixa da corrida pré-Lei 14.300

FONTES = ["CMU Bright", "Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"]


def aplicar() -> None:
    disponiveis = {f.name for f in font_manager.fontManager.ttflist}
    fonte = next((f for f in FONTES if f in disponiveis), "DejaVu Sans")
    plt.rcParams.update({
        "font.family": fonte,
        "font.size": 11,
        "text.color": TINTA,
        "axes.labelcolor": TINTA_SUAVE,
        "axes.edgecolor": NEUTRO,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 12,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "grid.color": GRADE,
        "grid.linewidth": 0.8,
        "xtick.color": TINTA_SUAVE,
        "ytick.color": TINTA_SUAVE,
        "legend.frameon": False,
        "figure.facecolor": "white",
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
    })


def fonte_rodape(fig, texto: str) -> None:
    """Linha de fonte no canto inferior esquerdo da figura."""
    fig.text(0.01, -0.02, texto, fontsize=8.5, color=TINTA_SUAVE, ha="left", va="top")
