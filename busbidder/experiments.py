"""Reproducible experiments that produce the evidence used in the report.

Run with:  python3 -m busbidder.experiments

Every output is written to ``examples/``. The dataset is seeded and the
ranking is deterministic, so running this again gives identical files.

Experiments
-----------
01 validation       what the loader rejected, corrected and held back
02 example search   the traveller request from the specification
03 profiles         four traveller profiles on three routes
04 weight sweep     how the winner changes as the price weight grows
05 normalisation    effect of outlier fences and minimum range (ablation)
06 missing CO2      emissions missing scored as worst (0) vs neutral (50)
07 company wins     which companies win across all routes and profiles
08 rank reversal    removing one offer changes the order of the others
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import config
from .criteria import MissingPolicy, RelativeScale, get_criterion, overridden
from .display import format_compare, format_search
from .loader import load_marketplace
from .models import Marketplace
from .profiles import compare_profiles, load_profiles
from .ranking import Ranker, search
from .request import TimeWindow, TravelRequest
from .weights import Weights

EXAMPLES_DIR = config.PROJECT_ROOT / "examples"
FIRST, LAST = date(2027, 7, 12), date(2027, 7, 14)


def example_request() -> TravelRequest:
    """The request in section 2 of the specification."""
    return TravelRequest("MAL", FIRST, LAST, max_price_eur=50,
                         preferred_departure=TimeWindow.parse("morning"),
                         max_transfers=1, require_changeable=True)


def open_request(code: str) -> TravelRequest:
    """A loose request used when comparing across all routes."""
    return TravelRequest(code, FIRST, LAST, preferred_departure=TimeWindow.parse("morning"))


# --- 01 ---------------------------------------------------------------------------


def exp_validation(data_dir: Path) -> List[str]:
    report = load_marketplace(data_dir, quarantine_dir=None).report
    lines = ["01 VALIDATION OF THE GENERATED DATASET", ""] + report.summary_lines() + [""]
    for issue in report.issues:
        lines.append(f"{issue.action.value:<10} row {issue.row:>5}  {issue.record_id:<26} "
                     f"{issue.field}: {issue.message}")
    return lines


# --- 02 ---------------------------------------------------------------------------


def exp_example_search(market: Marketplace) -> List[str]:
    result = search(market, example_request(), Weights.default())
    return (["02 EXAMPLE REQUEST FROM THE SPECIFICATION", ""]
            + format_search(result, "Malaga", top=20))


# --- 03 ---------------------------------------------------------------------------


def exp_profiles(market: Marketplace, profiles_file: Path, code: str) -> List[str]:
    profiles = list(load_profiles(profiles_file).values())
    results = compare_profiles(market, TravelRequest(code, FIRST, LAST), profiles)
    name = market.destinations[code].name
    return ([f"03 TRAVELLER PROFILES: MADRID -> {name.upper()}, 12-14 JULY 2027", ""]
            + format_compare(results, name, top=5))


# --- 04 ---------------------------------------------------------------------------


def exp_weight_sweep(market: Marketplace) -> List[Dict[str, object]]:
    """Raise the price weight from 10% to 70%. The other three core criteria share
    the rest in the default ratio 25:15:20, so only the emphasis on price changes."""
    rows = []
    for price_pct in range(10, 75, 5):
        rest = 100 - price_pct
        weights = Weights({
            "price": price_pct / 100,
            "duration": rest * 25 / 60 / 100,
            "departure": rest * 15 / 60 / 100,
            "flexibility": rest * 20 / 60 / 100,
        })
        result = search(market, open_request("MAL"), weights)
        best = result.best
        rows.append({
            "price_weight_pct": price_pct,
            "winner": best.offer.offer_id,
            "company": best.offer.company.name,
            "price_eur": best.offer.price_eur,
            "duration_min": best.offer.duration_min,
            "departure": f"{best.offer.departure:%H:%M}",
            "fare": best.offer.fare.fare_class.label,
            "total": best.total,
            "margin_to_second": round(best.total - result.ranked[1].total, 2),
        })
    return rows


# --- 05 ---------------------------------------------------------------------------


def _relative_variant(fences: bool, keep_min_range: bool):
    """Context managers that swap the scales of the relative criteria."""
    changes = []
    for name in ("price", "duration"):
        scale = get_criterion(name).scale
        changes.append((name, RelativeScale(scale.lower_is_better,
                                            scale.min_range if keep_min_range else 0.0,
                                            use_fences=fences)))
    return changes


def exp_normalisation(market: Marketplace) -> List[str]:
    variants = {
        "default (fences + minimum range)": (True, True),
        "no outlier fences": (False, True),
        "no minimum range": (True, False),
        "plain min-max": (False, False),
    }
    winners: Dict[str, Dict[str, str]] = defaultdict(dict)
    codes = sorted(market.destinations)
    for label, (fences, min_range) in variants.items():
        changes = _relative_variant(fences, min_range)
        with overridden(changes[0][0], scale=changes[0][1]), \
                overridden(changes[1][0], scale=changes[1][1]):
            for code in codes:
                result = search(market, open_request(code), Weights.default())
                winners[label][code] = result.best.offer.offer_id

    default = winners["default (fences + minimum range)"]
    lines = ["05 NORMALISATION ABLATION (default weights, 12-14 July, morning preference)", "",
             "Number of the 15 routes where the recommended offer changes compared with "
             "the default normalisation:"]
    for label in variants:
        changed = [c for c in codes if winners[label][c] != default[c]]
        detail = f"  ({', '.join(changed)})" if changed else ""
        lines.append(f"  {label:<34} {len(changed):>2} of {len(codes)}{detail}")
    lines.append("")
    lines.append(f"{'Route':<6}" + "".join(f"{label[:26]:<28}" for label in variants))
    for code in codes:
        lines.append(f"{code:<6}" + "".join(f"{winners[label][code]:<28}" for label in variants))
    return lines


# --- 06 ---------------------------------------------------------------------------


def exp_missing_emissions(market: Marketplace, profiles_file: Path) -> List[str]:
    eco = load_profiles(profiles_file)["eco"]
    lines = ["06 MISSING EMISSIONS: SCORED AS WORST (0) VERSUS NEUTRAL (50)",
             "Eco profile, 12-14 July. NocheVia does not report CO2.", "",
             f"{'Route':<6}{'Best NocheVia rank (worst)':>28}{'(neutral)':>12}"
             f"{'Winner (worst)':>28}{'Winner (neutral)':>28}"]
    for code in sorted(market.destinations):
        row = {}
        for policy in (MissingPolicy.WORST, MissingPolicy.NEUTRAL):
            with overridden("emissions", missing=policy):
                result = search(market, eco.apply(TravelRequest(code, FIRST, LAST)),
                                eco.weights)
            noc = [r.rank for r in result.ranked if r.offer.company.company_id == "NOC"]
            row[policy] = (min(noc) if noc else None, result.best.offer.offer_id)
        lines.append(f"{code:<6}{str(row[MissingPolicy.WORST][0]):>28}"
                     f"{str(row[MissingPolicy.NEUTRAL][0]):>12}"
                     f"{row[MissingPolicy.WORST][1]:>28}{row[MissingPolicy.NEUTRAL][1]:>28}")
    return lines


# --- 07 ---------------------------------------------------------------------------


def exp_company_wins(market: Marketplace, profiles_file: Path) -> List[Dict[str, object]]:
    profiles = load_profiles(profiles_file)
    setups = {"default": (None, Weights.default())}
    setups.update({pid: (p, p.weights) for pid, p in profiles.items()})
    rows = []
    for code in sorted(market.destinations):
        for setup, (profile, weights) in setups.items():
            request = TravelRequest(code, FIRST, LAST,
                                    preferred_departure=TimeWindow.parse("morning"))
            if profile is not None:
                request = profile.apply(request)
            result = search(market, request, weights)
            best_rank = {}
            for item in result.ranked:
                best_rank.setdefault(item.offer.company.name, item.rank)
            rows.append({"route": code, "setup": setup,
                         "winner_company": result.best.offer.company.name,
                         **{f"best_rank_{c.name}": best_rank.get(c.name, "")
                            for c in market.companies.values()}})
    return rows


def summarise_company_wins(rows: List[Dict[str, object]], market: Marketplace) -> List[str]:
    lines = ["07 WHICH COMPANIES WIN (15 routes x 5 setups = default weights + 4 profiles)", ""]
    setups = sorted({str(r["setup"]) for r in rows}, key=lambda s: (s != "default", s))
    names = [c.name for c in market.companies.values()]
    lines.append(f"{'Setup':<10}" + "".join(f"{n:>16}" for n in names))
    for setup in setups:
        counts = Counter(r["winner_company"] for r in rows if r["setup"] == setup)
        lines.append(f"{setup:<10}" + "".join(f"{counts.get(n, 0):>16}" for n in names))
    total = Counter(r["winner_company"] for r in rows)
    lines.append(f"{'all':<10}" + "".join(f"{total.get(n, 0):>16}" for n in names))
    lines.append("")
    lines.append("Average rank of each company's best offer (lower is better):")
    for n in names:
        ranks = [int(r[f"best_rank_{n}"]) for r in rows if r[f"best_rank_{n}"] != ""]
        avg = sum(ranks) / len(ranks) if ranks else float("nan")
        lines.append(f"  {n:<16} {avg:5.1f}  (present in {len(ranks)} of {len(rows)} rankings)")
    return lines


# --- 08 ---------------------------------------------------------------------------


def exp_rank_reversal(market: Marketplace) -> List[str]:
    """Relative scales depend on which offers are present. Remove each acceptable
    offer of the example request in turn and report when the order of the
    remaining offers changes."""
    request = example_request()
    weights = Weights.default()
    result = search(market, request, weights)
    base = [r.offer.offer_id for r in result.ranked]
    lines = ["08 RANK REVERSAL: REMOVE ONE OFFER, RE-RANK THE REST (example request)", "",
             "Original order:"]
    lines += [f"  #{r.rank} {r.offer.offer_id:<26} {r.offer.duration_min:>4} min  "
              f"EUR {r.offer.price_eur:6.2f}  total {r.total:6.2f}" for r in result.ranked]
    changed = 0
    for removed in result.ranked:
        rest = [r.offer for r in result.ranked if r is not removed]
        reranked = Ranker(weights).rank(rest, request)
        new = [r.offer.offer_id for r in reranked]
        expected = [b for b in base if b != removed.offer.offer_id]
        if new != expected:
            changed += 1
            lines.append("")
            lines.append(f"Removing #{removed.rank} {removed.offer.offer_id} changes the order:")
            for r in reranked:
                old = base.index(r.offer.offer_id) + 1
                note = "" if old - (old > removed.rank) == r.rank else f"  (was #{old})"
                lines.append(f"  #{r.rank} {r.offer.offer_id:<26} total {r.total:6.2f}{note}")
    lines.append("")
    lines.append(f"{changed} of {len(result.ranked)} removals change the order of the "
                 "remaining offers.")
    return lines


# --- runner -------------------------------------------------------------------------


def _write_lines(path: Path, lines: Sequence[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_all(out_dir: Path = EXAMPLES_DIR, data_dir: Optional[Path] = None) -> List[Path]:
    data_dir = Path(data_dir) if data_dir else config.DATA_DIR
    profiles_file = data_dir / "profiles.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    market = load_marketplace(data_dir, quarantine_dir=None).marketplace
    written: List[Path] = []

    def lines(name: str, content: Sequence[str]) -> None:
        _write_lines(out_dir / name, content)
        written.append(out_dir / name)

    lines("01_validation.txt", exp_validation(data_dir))
    lines("02_example_search.txt", exp_example_search(market))
    for code in ("MAL", "BAR", "SAL"):
        lines(f"03_profiles_{code}.txt", exp_profiles(market, profiles_file, code))

    sweep = exp_weight_sweep(market)
    _write_csv(out_dir / "04_weight_sweep.csv", sweep)
    written.append(out_dir / "04_weight_sweep.csv")
    sweep_lines = ["04 WEIGHT SWEEP: PRICE WEIGHT 10% TO 70% (Malaga, 12-14 July, morning)",
                   "", f"{'Price %':>8}  {'Winner':<26}{'EUR':>7}{'Min':>6}  {'Dep':<6}"
                   f"{'Fare':<9}{'Total':>7}{'Margin':>8}"]
    for r in sweep:
        sweep_lines.append(f"{r['price_weight_pct']:>8}  {r['winner']:<26}{r['price_eur']:>7.2f}"
                           f"{r['duration_min']:>6}  {r['departure']:<6}{r['fare']:<9}"
                           f"{r['total']:>7.2f}{r['margin_to_second']:>8.2f}")
    lines("04_weight_sweep.txt", sweep_lines)

    lines("05_normalisation.txt", exp_normalisation(market))
    lines("06_missing_emissions.txt", exp_missing_emissions(market, profiles_file))

    wins = exp_company_wins(market, profiles_file)
    _write_csv(out_dir / "07_company_wins.csv", wins)
    written.append(out_dir / "07_company_wins.csv")
    lines("07_company_wins.txt", summarise_company_wins(wins, market))
    lines("08_rank_reversal.txt", exp_rank_reversal(market))
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the BusBidder report experiments.")
    parser.add_argument("--out", type=Path, default=EXAMPLES_DIR)
    args = parser.parse_args(argv)
    for path in run_all(args.out):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
