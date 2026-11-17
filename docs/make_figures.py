"""Draw the report figures from the saved experiment outputs in examples/.

    python3 docs/make_figures.py

This script is only used to produce images for the report. It needs
matplotlib, which the application itself does not use (pip install matplotlib).
It reads only the files in examples/, so the figures always match the
evidence that ``python3 -m busbidder.experiments`` regenerates.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
OUT = ROOT / "docs" / "figures"

# Validated categorical palette (light mode), one fixed colour per company.
COMPANY_COLOURS = {
    "SolRuta": "#2a78d6",
    "Camino Express": "#eb6834",
    "CostaLink": "#1baf7a",
    "VerdeBus": "#008300",
    "NocheVia": "#e87ba4",
}
COMPANIES = list(COMPANY_COLOURS)
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#ffffff"


def style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT_2,
        "axes.titlecolor": TEXT,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "xtick.color": TEXT_2,
        "ytick.color": TEXT_2,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
    })


def weight_sweep() -> Path:
    with open(EXAMPLES / "04_weight_sweep.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    x = [int(r["price_weight_pct"]) for r in rows]
    y = [float(r["price_eur"]) for r in rows]

    fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=200)
    ax.step(x, y, where="mid", color=TEXT_2, linewidth=1.2, zorder=1)
    for r, xi, yi in zip(rows, x, y):
        ax.scatter(xi, yi, s=46, color=COMPANY_COLOURS[r["company"]], zorder=3,
                   edgecolors=SURFACE, linewidths=1.5)

    # One direct label per run of identical winners
    runs = []
    for r, xi, yi in zip(rows, x, y):
        if runs and runs[-1]["winner"] == r["winner"]:
            runs[-1]["x"].append(xi)
        else:
            runs.append({"winner": r["winner"], "x": [xi], "y": yi, "row": r})
    # Label placement per run, chosen so no label touches a line or another label
    placements = [((0, 9), "center", "bottom"), ((0, 9), "center", "bottom"),
                  ((6, 6), "left", "bottom"), ((0, -10), "center", "top")]
    for i, run in enumerate(runs):
        r = run["row"]
        label = f"{r['company']} {r['fare']}\nEUR {float(r['price_eur']):.2f}, {r['departure']}"
        anchor_x = sum(run["x"]) / len(run["x"])
        offset, ha, va = placements[i % len(placements)]
        ax.annotate(label, (anchor_x, run["y"]), xytext=offset, textcoords="offset points",
                    ha=ha, va=va, fontsize=8.5, color=TEXT)

    ax.axvline(40, color=GRID, linewidth=1, linestyle="--", zorder=0)
    ax.text(40.6, 13, "default weight 40%", fontsize=8, color=TEXT_2, va="center", ha="left")
    ax.set_xlim(7, 73)
    ax.set_ylim(10, 95)
    ax.set_xticks(x)
    ax.set_xlabel("Price weight (%)  (other core criteria share the rest 25:15:20)")
    ax.set_ylabel("Price of recommended offer (EUR)")
    ax.set_title("Recommended offer as the price weight increases")
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.text(0.01, 0.01, "Madrid to Malaga, 12-14 July 2027, morning preference, no other "
             "constraints. Source: examples/04_weight_sweep.csv", fontsize=7.5, color=TEXT_2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    path = OUT / "fig_weight_sweep.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def company_wins() -> Path:
    with open(EXAMPLES / "07_company_wins.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    setups = ["default", "budget", "business", "flexible", "eco"]
    counts = {s: Counter(r["winner_company"] for r in rows if r["setup"] == s) for s in setups}

    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=200)
    n = len(COMPANIES)
    width = 0.16
    gap = 0.012
    for i, company in enumerate(COMPANIES):
        xs = [k + (i - (n - 1) / 2) * (width + gap) for k in range(len(setups))]
        values = [counts[s].get(company, 0) for s in setups]
        ax.bar(xs, values, width=width, color=COMPANY_COLOURS[company], label=company,
               zorder=2)
        for xi, v in zip(xs, values):
            ax.text(xi, v + 0.25, str(v), ha="center", va="bottom", fontsize=7, color=TEXT_2)
    ax.set_xticks(range(len(setups)))
    ax.set_xticklabels(["default weights", "budget", "business", "flexible", "eco"])
    ax.set_ylabel("Routes won (of 15)")
    ax.set_ylim(0, 17)
    ax.set_title("Which company is recommended, per setup")
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(ncol=5, frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(0, 1.02),
              handlelength=1, columnspacing=1.2)
    fig.text(0.01, 0.01, "15 routes, 12-14 July 2027, morning preference. NocheVia wins 0 "
             "of 75. Source: examples/07_company_wins.csv", fontsize=7.5, color=TEXT_2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    path = OUT / "fig_company_wins.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def missing_emissions() -> Path:
    text = (EXAMPLES / "06_missing_emissions.txt").read_text(encoding="utf-8")
    data = []
    for line in text.splitlines():
        match = re.match(r"^([A-Z]{3})\s+(\d+)\s+(\d+)\s", line)
        if match:
            data.append((match.group(1), int(match.group(2)), int(match.group(3))))
    data.sort(key=lambda d: d[1] - d[2])

    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=200)
    worst_c, neutral_c = "#e34948", "#2a78d6"
    for i, (code, worst, neutral) in enumerate(data):
        ax.plot([neutral, worst], [i, i], color=GRID, linewidth=2, zorder=1)
        ax.scatter(neutral, i, s=40, color=neutral_c, zorder=3, edgecolors=SURFACE,
                   linewidths=1.5, label="missing CO2 scored neutral (50)" if i == 0 else None)
        ax.scatter(worst, i, s=40, color=worst_c, zorder=3, edgecolors=SURFACE,
                   linewidths=1.5, label="missing CO2 scored worst (0), as implemented"
                   if i == 0 else None)
        ax.text(worst + 0.8, i, f"-{worst - neutral}", va="center", fontsize=7.5, color=TEXT_2)
    ax.set_yticks(range(len(data)))
    ax.set_yticklabels([d[0] for d in data])
    ax.set_xlim(0, 55)
    ax.set_xlabel("Rank of NocheVia's best offer for the eco profile (1 = recommended)")
    ax.set_title("Cost of NocheVia not reporting emissions", pad=22)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2,
              handletextpad=0.3, columnspacing=1.5)
    fig.text(0.01, 0.01, "Eco profile, 12-14 July 2027. The winner (VerdeBus) is the same "
             "under both policies. Source: examples/06_missing_emissions.txt",
             fontsize=7.5, color=TEXT_2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    path = OUT / "fig_missing_emissions.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    style()
    for path in (weight_sweep(), company_wins(), missing_emissions()):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
