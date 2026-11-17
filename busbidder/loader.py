"""Reads companies, destinations and offers from file and validates them.

companies.json has a few descriptive records so JSON fits. destinations.csv
and offers.csv are flat tables, and offers.csv is big so it's handy to be
able to open it in a spreadsheet.

If a whole file is wrong (missing, unreadable, wrong columns) a DataFileError
is raised and the program stops, since nothing useful can be done without
it. Problems with single records are handled per record, see validation.py.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from . import config
from .exceptions import DataFileError, DuplicateRecordError, RecordError, SuspiciousRecordError
from .models import Company, Destination, Marketplace
from .validation import (
    COMPANY_FIELDS,
    DESTINATION_FIELDS,
    OFFER_FIELDS,
    Action,
    Issue,
    OfferValidator,
    issue_from_correction,
    validate_company,
    validate_destination,
)

logger = logging.getLogger(__name__)


@dataclass
class LoadReport:
    """Counts and findings from one load, used by the CLI and the report."""

    rows_read: int = 0
    accepted: int = 0
    corrected_records: int = 0
    rejected: int = 0
    review: int = 0
    sold_out: int = 0
    issues: List[Issue] = field(default_factory=list)

    def summary_lines(self) -> List[str]:
        return [
            f"Offer rows read:          {self.rows_read}",
            f"Accepted:                 {self.accepted}"
            f" (of which corrected: {self.corrected_records}, sold out: {self.sold_out})",
            f"Rejected:                 {self.rejected}",
            f"Held back for review:     {self.review}",
        ]


@dataclass
class LoadResult:
    marketplace: Marketplace
    report: LoadReport


class DataLoader:
    def __init__(self,
                 companies_path: Path = config.COMPANIES_FILE,
                 destinations_path: Path = config.DESTINATIONS_FILE,
                 offers_path: Path = config.OFFERS_FILE,
                 quarantine_dir: Optional[Path] = config.QUARANTINE_DIR) -> None:
        self.companies_path = Path(companies_path)
        self.destinations_path = Path(destinations_path)
        self.offers_path = Path(offers_path)
        self.quarantine_dir = Path(quarantine_dir) if quarantine_dir else None

    # Public API

    def load(self) -> LoadResult:
        report = LoadReport()
        companies = self.load_companies(report)
        destinations = self.load_destinations(report)
        market = Marketplace(companies=companies, destinations=destinations)
        self.load_offers(market, report)
        for line in report.summary_lines():
            logger.info(line.strip())
        return LoadResult(marketplace=market, report=report)

    # Reference data

    def load_companies(self, report: LoadReport) -> Dict[str, Company]:
        records = self._read_json_list(self.companies_path, "companies")
        companies: Dict[str, Company] = {}
        source = self.companies_path.name
        for index, raw in enumerate(records, start=1):
            record_id = str(raw.get("company_id", "?")) if isinstance(raw, dict) else "?"
            try:
                if not isinstance(raw, dict):
                    raise DataFileError(f"{source}: entry {index} is not an object")
                company = validate_company(raw)
                if company.company_id in companies:
                    raise DuplicateRecordError("company_id", company.company_id, "duplicate company id")
            except RecordError as err:
                self._record_issue(report, Issue(source, index, record_id, Action.REJECTED,
                                                 err.field, str(err.value), err.message))
                continue
            companies[company.company_id] = company
        if not companies:
            raise DataFileError(f"{source}: no valid companies found")
        logger.info("Loaded %d companies from %s", len(companies), source)
        return companies

    def load_destinations(self, report: LoadReport) -> Dict[str, Destination]:
        destinations: Dict[str, Destination] = {}
        source = self.destinations_path.name
        for row_no, row in self._read_csv(self.destinations_path, DESTINATION_FIELDS):
            try:
                dest = validate_destination(row)
                if dest.code in destinations:
                    raise DuplicateRecordError("code", dest.code, "duplicate destination code")
            except RecordError as err:
                self._record_issue(report, Issue(source, row_no, row.get("code") or "?",
                                                 Action.REJECTED, err.field, str(err.value),
                                                 err.message))
                continue
            destinations[dest.code] = dest
        if not destinations:
            raise DataFileError(f"{source}: no valid destinations found")
        logger.info("Loaded %d destinations from %s", len(destinations), source)
        return destinations

    # Offers

    def load_offers(self, market: Marketplace, report: LoadReport) -> None:
        validator = OfferValidator(market.companies, market.destinations)
        source = self.offers_path.name
        rejected_rows: List[Tuple[int, Mapping[str, Any], RecordError]] = []
        review_rows: List[Tuple[int, Mapping[str, Any], RecordError]] = []

        for row_no, row in self._read_csv(self.offers_path, OFFER_FIELDS):
            report.rows_read += 1
            record_id = (row.get("offer_id") or "?").strip() or "?"
            try:
                if record_id in market.offers:
                    raise DuplicateRecordError("offer_id", record_id, "duplicate offer id")
                offer, corrections = validator.validate(row)
            except SuspiciousRecordError as err:
                report.review += 1
                review_rows.append((row_no, row, err))
                self._record_issue(report, Issue(source, row_no, record_id, Action.REVIEW,
                                                 err.field, str(err.value), err.message))
                continue
            except RecordError as err:
                report.rejected += 1
                rejected_rows.append((row_no, row, err))
                self._record_issue(report, Issue(source, row_no, record_id, Action.REJECTED,
                                                 err.field, str(err.value), err.message))
                continue

            for correction in corrections:
                self._record_issue(report, issue_from_correction(source, row_no, record_id,
                                                                 correction))
            if corrections:
                report.corrected_records += 1
            if offer.is_sold_out:
                report.sold_out += 1
            market.add_offer(offer)
            report.accepted += 1

        if report.accepted == 0:
            raise DataFileError(f"{source}: no valid offers found")
        logger.info("Loaded %d offers from %s", report.accepted, source)
        self._write_quarantine("rejected_offers.csv", rejected_rows)
        self._write_quarantine("review_offers.csv", review_rows)

    # Helpers

    @staticmethod
    def _record_issue(report: LoadReport, issue: Issue) -> None:
        report.issues.append(issue)
        level = logging.INFO if issue.action is Action.CORRECTED else logging.WARNING
        logger.log(level, "%s", issue)

    @staticmethod
    def _read_csv(path: Path, expected_fields: List[str]) -> Iterator[Tuple[int, Dict[str, str]]]:
        """Yield (line number, row) pairs after checking the header."""
        try:
            handle = open(path, newline="", encoding="utf-8")
        except FileNotFoundError:
            raise DataFileError(f"Data file not found: {path}") from None
        except OSError as err:
            raise DataFileError(f"Cannot read {path}: {err}") from None
        with handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            missing = [name for name in expected_fields if name not in header]
            if missing:
                raise DataFileError(f"{path.name}: missing column(s): {', '.join(missing)}")
            for row in reader:
                # Line number of the row in the file (header is line 1)
                yield reader.line_num, row

    @staticmethod
    def _read_json_list(path: Path, key: str) -> List[Any]:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            raise DataFileError(f"Data file not found: {path}") from None
        except json.JSONDecodeError as err:
            raise DataFileError(f"{path.name} is not valid JSON: {err}") from None
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise DataFileError(f"{path.name}: expected an object with a '{key}' list")
        return data[key]

    def _write_quarantine(self, filename: str,
                          rows: List[Tuple[int, Mapping[str, Any], RecordError]]) -> None:
        if self.quarantine_dir is None:
            return
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        path = self.quarantine_dir / filename
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["source_row", "problem_field", "reason", *OFFER_FIELDS])
            for row_no, row, err in rows:
                writer.writerow([row_no, err.field, err.message,
                                 *(row.get(name, "") for name in OFFER_FIELDS)])
        logger.info("Wrote %d record(s) to %s", len(rows), path)


def load_marketplace(data_dir: Optional[Path] = None,
                     quarantine_dir: Optional[Path] = config.QUARANTINE_DIR) -> LoadResult:
    """Load the three standard files from one folder."""
    data_dir = Path(data_dir) if data_dir else config.DATA_DIR
    loader = DataLoader(
        companies_path=data_dir / config.COMPANIES_FILE.name,
        destinations_path=data_dir / config.DESTINATIONS_FILE.name,
        offers_path=data_dir / config.OFFERS_FILE.name,
        quarantine_dir=quarantine_dir,
    )
    return loader.load()
