# BusBidder Spain

A modular Python application that filters and ranks competing bus offers from
Madrid for an individual traveller, explains the recommendation and makes a
simulated booking. Final project for ACIT4420 Problem-solving with scripting.

> Status: Stage 1 (data, validation, logging). Ranking, profiles, booking and
> the full CLI follow in later stages.

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
