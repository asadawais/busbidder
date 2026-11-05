# BusBidder Spain

A modular Python application that filters and ranks competing bus offers from
Madrid for an individual traveller, explains the recommendation and makes a
simulated booking. Final project for ACIT4420 Problem-solving with scripting.

> Status: Stage 2 (data, validation, filtering and weighted ranking).
> Explanations, traveller profiles and booking follow in later stages.

## Requirements

* Python 3.10 or newer (developed with Python 3.15, also tested on 3.10)
* No third-party libraries. Only the standard library is used.

## Project layout

```
busbidder/              application package
  config.py             file locations and constants
  exceptions.py         custom exception hierarchy
  models.py             domain classes (Company, Destination, FareConditions, Offer, Marketplace)
  validation.py         field parsers, regex identifiers, offer validator
  loader.py             reads the files, applies the reject/correct/review policy
  logging_setup.py      log file configuration
  data_generator.py     seeded generator for the simulated dataset
  request.py            travel request and departure time windows
  constraints.py        hard constraints (registry decorator) and the filter
  criteria.py           scoring criteria (registry decorator) and normalisation
  weights.py            weight parsing and validation
  ranking.py            weighted ranking, tie-breaking, criterion influence
  display.py            text tables for the CLI
  cli.py                command-line interface
data/                   input data (generated with seed 42)
  companies.json
  destinations.csv
  offers.csv
  injected_faults.json  list of deliberately faulty records and their expected outcome
tests/                  unit tests (unittest)
logs/                   log file (created at run time)
output/quarantine/      rejected and review records (created at run time)
```

## Commands

Run all commands from the project root.

Regenerate the dataset (optional, the files are already included):

```
python3 -m busbidder.data_generator --seed 42
```

Load and validate the data, listing every rejected, corrected and review record:

```
python3 -m busbidder validate -v
```

Search and rank offers (the example request from the specification):

```
python3 -m busbidder search --to Malaga --from 2027-07-12 --until 2027-07-14 --budget 50 --depart morning --max-transfers 1 --changeable
```

Use custom weights (percentages or fractions, must total 100 or 1.0):

```
python3 -m busbidder search --to Malaga --from 2027-07-12 --until 2027-07-14 --weights price=30,duration=20,departure=10,flexibility=20,emissions=20
```

Run `python3 -m busbidder search --help` for every option.

Run the tests:

```
python3 -m unittest discover -v
```

## Data validation policy

| Outcome   | When                                                        | Where it goes |
|-----------|-------------------------------------------------------------|---------------|
| Rejected  | impossible or unusable values (negative price, unknown destination, invalid date, arrival before departure, rating outside 1-5, malformed or duplicate id) | `output/quarantine/rejected_offers.csv` |
| Corrected | intended value is unambiguous (comma decimal, letter case, duration that disagrees with the times) | kept, correction logged |
| Review    | possible but implausible (average speed above 110 km/h, price above EUR 250) | `output/quarantine/review_offers.csv` |

Offers with zero seats are valid (sold out) and are removed later by the
seat-availability constraint.

## Hard constraints and soft preferences

| Hard constraints (offer removed) | Soft preferences (affect the score) |
|---|---|
| destination, travel window, seats for all passengers, maximum ticket price, maximum transfers, changeable ticket, refundable ticket, minimum checked bags, maximum journey time | preferred departure window, criterion weights |

## Scoring

Each criterion is converted to a 0-100 score, then combined with the weights:
`total = sum(weight x score)`.

| Criterion | Scale |
|---|---|
| price, duration, emissions | relative to the other acceptable offers (best = 100), with outlier fences and a minimum range (EUR 5, 30 min, 2 kg) |
| departure | 100 inside the preferred window, falling to 0 at 4 hours outside |
| flexibility | 60% change policy (none 0, fee 50, free 100) + 40% refund policy (none 0, partial 50, full 100) |
| transfers (optional) | 0 -> 100, 1 -> 60, 2 -> 25, 3 -> 0 |
| rating (optional) | 1.0 -> 0, 5.0 -> 100, missing -> 50 |

Missing emissions score 0. Default weights: price 40%, duration 25%,
departure 15%, flexibility 20%. Ties are broken by price, duration,
departure time and offer id.
