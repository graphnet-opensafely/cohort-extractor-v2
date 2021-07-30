from pathlib import Path

import pytest

from cohortextractor import codelist, table
from cohortextractor.csv_utils import is_csv_filename, write_rows_to_csv
from cohortextractor.query_utils import get_column_definitions
from cohortextractor.validate_dummy_data import (
    SUPPORTED_FILE_FORMATS,
    DummyDataValidationError,
    validate_dummy_data,
)


cl = codelist(["12345"], system="snomed")

fixtures_path = Path(__file__).parent / "fixtures" / "dummy_data"


class Cohort:
    population = table("practice_registations").exists()
    sex = table("patients").latest().get("sex")
    _code = table("clinical_events").filter(code__in=cl)
    has_event = _code.exists()
    event_date = _code.latest().get("date")
    event_count = _code.count("code")


column_definitions = get_column_definitions(Cohort)


@pytest.mark.parametrize("file_format", SUPPORTED_FILE_FORMATS)
def test_validate_dummy_data_valid(file_format, tmpdir):
    rows = zip(
        ["patient_id", "11", "22"],
        ["sex", "F", "M"],
        ["has_event", True, False],
        ["event_date", "2021-01-01", None],
        ["event_count", 1, None],
    )
    dummy_data_file = Path(tmpdir) / f"dummy-data.{file_format}"
    if is_csv_filename(dummy_data_file):
        write_rows_to_csv(rows, dummy_data_file)

    validate_dummy_data(column_definitions, dummy_data_file)


@pytest.mark.parametrize(
    "filename,error_fragment",
    [
        ("missing-column", "Missing column in dummy data: event_date"),
        ("extra-column", "Unexpected column in dummy data: extra_col"),
        ("invalid-bool", "Invalid value `'X'` for has_event"),
        ("invalid-date", "Invalid value `'2021-021-021'` for event_date"),
        ("invalid-patient-id", "Invalid value `'Eleven'` for patient_id"),
    ],
)
def test_validate_dummy_data_invalid_csv(filename, error_fragment):
    with pytest.raises(DummyDataValidationError, match=error_fragment):
        validate_dummy_data(column_definitions, fixtures_path / f"{filename}.csv")


def test_validate_dummy_data_unknown_file_extension():
    with pytest.raises(DummyDataValidationError):
        validate_dummy_data(column_definitions, fixtures_path / "data.txt")


@pytest.mark.parametrize("file_format", SUPPORTED_FILE_FORMATS)
def test_validate_dummy_data_missing_data_file(file_format):
    with pytest.raises(DummyDataValidationError):
        validate_dummy_data(
            column_definitions, fixtures_path / f"missing.{file_format}"
        )
