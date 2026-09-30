"""
plot_indice_now.py
====================
Genere un PNG statique de l'historique mensuel de l'Indice NOW pour les
deux raccordements (HTB1_LU, HTA_LU), a partir de
`indice_now_mensuel_HTB1_LU.csv` / `indice_now_mensuel_HTA_LU.csv`
(meme dossier). Auto-suffisant : n'a besoin que de ces deux CSV, pas de la
source horaire.

Style aligne sur `Index_NoW/plot_croisement_gaz_elec.py` (meme palette
categorielle bleu/orange).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

DOSSIER = Path(__file__).resolve().parent

RACCORDEMENTS = {
    "HTB1_LU": {"label": "Grand industriel (HTB1/LU, RTE)", "couleur": "#2a78d6"},
    "HTA_LU": {"label": "Industriel (HTA/LU, Enedis)", "couleur": "#eb6834"},
}

COULEUR_TEXTE_PRIMAIRE = "#0b0b0b"
COULEUR_TEXTE_SECONDAIRE = "#52514e"
COULEUR_TEXTE_MUTED = "#898781"
COULEUR_GRILLE = "#e1e0d9"
COULEUR_AXE = "#c3c2b7"
COULEUR_SURFACE = "#fcfcfb"


def charger(nom: str) -> pd.DataFrame:
    df = pd.read_csv(DOSSIER / f"indice_now_mensuel_{nom}.csv")
    df["date"] = pd.to_datetime(df["annee_mois"], format="%Y-%m")
    return df


def main() -> None:
    fig, ax = plt.subplots(figsize=(12, 5), dpi=200)
    fig.patch.set_facecolor(COULEUR_SURFACE)
    ax.set_facecolor(COULEUR_SURFACE)

    for nom, style in RACCORDEMENTS.items():
        df = charger(nom)
        ax.plot(
            df["date"], df["indice_now_eur_mwh"],
            color=style["couleur"], linewidth=2, solid_capstyle="round", solid_joinstyle="round",
            label=style["label"],
        )
        # Marqueur + label directs au dernier point de chaque serie.
        dernier = df.iloc[-1]
        ax.scatter(
            [dernier["date"]], [dernier["indice_now_eur_mwh"]],
            s=64, color=style["couleur"], zorder=5,
            edgecolors=COULEUR_SURFACE, linewidths=2,
        )
        ax.annotate(
            f"{dernier['indice_now_eur_mwh']:.1f} EUR/MWh",
            xy=(dernier["date"], dernier["indice_now_eur_mwh"]),
            xytext=(8, 0), textcoords="offset points",
            va="center", fontsize=9, color=COULEUR_TEXTE_SECONDAIRE,
        )

    ax.set_title("Indice NOW — historique mensuel", fontsize=14, color=COULEUR_TEXTE_PRIMAIRE, pad=28, loc="left")
    ax.text(
        0.0, 1.06, "Economie de l'arbitrage gaz/electrique vs gaz seul, EUR/MWh",
        transform=ax.transAxes, fontsize=10, color=COULEUR_TEXTE_SECONDAIRE,
    )

    ax.set_ylabel("EUR/MWh", fontsize=10, color=COULEUR_TEXTE_MUTED)
    ax.yaxis.grid(True, color=COULEUR_GRILLE, linewidth=1)
    ax.set_axisbelow(True)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(COULEUR_AXE)
    ax.spines["bottom"].set_color(COULEUR_AXE)
    ax.tick_params(colors=COULEUR_TEXTE_MUTED, labelsize=9)

    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=(4, 7, 10)))

    legende = ax.legend(
        loc="upper left", frameon=False, fontsize=9,
        labelcolor=COULEUR_TEXTE_SECONDAIRE,
    )

    fig.tight_layout()
    out_path = DOSSIER / "indice_now_mensuel.png"
    fig.savefig(out_path, facecolor=COULEUR_SURFACE)
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
