import shutil
from pathlib import Path

import numpy
import pandas
import pytest

import databuilder.measure as measure
from databuilder.measure import Measure, combine_csv_files_with_dates

from .lib.util import RecordingReporter, null_reporter


def test_calculates_quotients():
    m = Measure("ignored-id", numerator="fish", denominator="litres")
    data = pandas.DataFrame(
        {"fish": [10, 20, 50], "litres": [1, 2, 100]},
        index=["small bowl", "large bowl", "pond"],
    )
    result = calculate(m, data)

    assert result.loc["small bowl"]["value"] == 10.0
    assert result.loc["large bowl"]["value"] == 10.0
    assert result.loc["pond"]["value"] == 0.5


def test_groups_data_together():
    m = Measure("ignored-id", numerator="fish", denominator="litres", group_by="colour")
    data = pandas.DataFrame(
        {"fish": [10, 20], "litres": [1, 2], "colour": ["gold", "gold"]},
        index=["small bowl", "large bowl"],
    )
    result = calculate(m, data)
    result.set_index("colour", inplace=True)

    assert result.loc["gold"]["fish"] == 30
    assert result.loc["gold"]["litres"] == 3
    assert result.loc["gold"]["value"] == 10.0


def test_groups_into_multiple_buckets():
    m = Measure("ignored-id", numerator="fish", denominator="litres", group_by="colour")
    data = pandas.DataFrame(
        {"fish": [10, 10], "litres": [1, 2], "colour": ["gold", "pink"]}
    )
    result = calculate(m, data)
    result.set_index("colour", inplace=True)

    assert result.loc["gold"]["value"] == 10.0
    assert result.loc["pink"]["value"] == 5.0


def test_groups_by_multiple_columns():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        group_by=["colour", "nationality"],
    )
    data = pandas.DataFrame(
        {
            "fish": [10, 20, 40, 80],
            "litres": [1, 1, 1, 1],
            "colour": ["gold", "gold", "gold", "pink"],
            "nationality": ["russian", "japanese", "russian", "french"],
        }
    )
    result = calculate(m, data)

    assert result.iloc[0]["colour"] == "gold"
    assert result.iloc[0]["nationality"] == "japanese"
    assert result.iloc[0]["fish"] == 20
    assert result.iloc[1]["colour"] == "gold"
    assert result.iloc[1]["nationality"] == "russian"
    assert result.iloc[1]["fish"] == 50
    assert result.iloc[2]["colour"] == "pink"
    assert result.iloc[2]["nationality"] == "french"
    assert result.iloc[2]["fish"] == 80


@pytest.mark.parametrize(
    "group_by,error",
    [
        ("fish", "Column 'fish' appears in both numerator and group_by"),
        ("litres", "Column 'litres' appears in both denominator and group_by"),
    ],
)
def test_cant_group_by_numerator_or_denominator(group_by, error):
    with pytest.raises(ValueError, match=error):
        Measure("ignored-id", numerator="fish", denominator="litres", group_by=group_by)


def test_can_group_by_population():
    """Grouping by the special population variable returns one row for the whole dataset"""
    m = Measure(
        "ignored-id", numerator="fish", denominator="population", group_by="population"
    )
    data = pandas.DataFrame(
        {
            "fish": [4, 5, 2, 2, 3, 2],
            "litres": [2, 2, 2, 2, 3, 3],
            "population": [1, 1, 1, 1, 1, 1],
        }
    )
    result = calculate(m, data)
    assert len(result) == 1
    assert dict(result.iloc[0]) == {"fish": 18.0, "population": 6.0, "value": 3.0}


def test_throws_away_unused_columns():
    m = Measure("ignored-id", numerator="fish", denominator="litres")
    data = pandas.DataFrame(
        {"fish": [10], "litres": [1], "colour": ["green"], "clothing": ["trousers"]}
    )
    result = calculate(m, data)
    assert "clothing" not in result.iloc[0]

    m = Measure("ignored-id", numerator="fish", denominator="litres", group_by="colour")
    data = pandas.DataFrame(
        {"fish": [10], "litres": [1], "colour": ["green"], "age": [12]}
    )
    result = calculate(m, data)
    assert "age" not in result.iloc[0]


def test_suppresses_small_numbers_in_the_numerator():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame({"fish": [1], "litres": [100]}, index=["bowl"])
    result = calculate(m, data)

    assert numpy.isnan(result.loc["bowl"]["fish"])
    assert numpy.isnan(result.loc["bowl"]["value"])


def test_suppresses_small_numbers_at_threshold_in_the_numerator():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {
            "fish": [
                measure.SMALL_NUMBER_THRESHOLD,
                measure.SMALL_NUMBER_THRESHOLD,
                measure.SMALL_NUMBER_THRESHOLD + 1,
            ],
            "litres": [100, 100, measure.SMALL_NUMBER_THRESHOLD + 1],
        },
        index=["bowl", "box", "bag"],
    )
    result = calculate(m, data)

    assert numpy.isnan(result.loc["bowl"]["fish"])
    assert numpy.isnan(result.loc["box"]["fish"])
    assert result.loc["bag"]["value"] == 1.0


def test_suppresses_small_numbers_after_grouping():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        group_by="colour",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {
            "fish": [2, 2, 2, 2, 3, 3],
            "litres": [2, 2, 2, 2, 3, 3],
            "colour": ["gold", "gold", "bronze", "bronze", "pink", "pink"],
        }
    )
    result = calculate(m, data)
    result.set_index("colour", inplace=True)

    assert numpy.isnan(result.loc["gold"]["value"])
    assert numpy.isnan(result.loc["bronze"]["value"])
    assert result.loc["pink"]["value"] == 1.0


def test_suppression_doesnt_affect_later_calculations_on_the_same_data():
    data = pandas.DataFrame({"fish": [2], "litres": [2]})

    m1 = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    r1 = calculate(m1, data)
    assert numpy.isnan(r1.iloc[0]["value"])

    m2 = Measure("ignored-id", numerator="fish", denominator="litres")
    r2 = calculate(m2, data)
    assert r2.iloc[0]["value"] == 1.0


def test_doesnt_suppress_zero_values():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {"fish": [0, 1], "litres": [100, 100]}, index=["bowl", "bag"]
    )
    result = calculate(m, data)

    assert result.loc["bowl"]["fish"] == 0
    assert result.loc["bowl"]["value"] == 0


def test_suppresses_denominator_if_its_small_enough():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame({"fish": [0], "litres": [4]}, index=["bag"])
    result = calculate(m, data)

    assert numpy.isnan(result.loc["bag"]["litres"])
    assert numpy.isnan(result.loc["bag"]["value"])


def test_suppresses_an_extra_value_if_total_of_small_values_is_less_than_threshold():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {"fish": [2, 2, 6], "litres": [10, 10, 10]}, index=["a", "b", "c"]
    )
    result = calculate(m, data)

    assert numpy.isnan(result.loc["a"]["fish"])
    assert numpy.isnan(result.loc["b"]["fish"])
    assert numpy.isnan(result.loc["c"]["fish"])


def test_suppresses_all_small_values_even_if_total_is_way_over_threshold():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {"fish": [2, 2, 2, 2], "litres": [10, 10, 10, 10]}, index=["a", "b", "c", "d"]
    )
    result = calculate(m, data)

    assert numpy.isnan(result.loc["a"]["fish"])
    assert numpy.isnan(result.loc["b"]["fish"])
    assert numpy.isnan(result.loc["c"]["fish"])
    assert numpy.isnan(result.loc["d"]["fish"])


def test_suppresses_smallest_extra_value_to_reach_threshold():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {"fish": [2, 10, 8], "litres": [10, 10, 10]}, index=["a", "b", "c"]
    )
    result = calculate(m, data)

    assert numpy.isnan(result.loc["a"]["fish"])
    assert result.loc["b"]["fish"] == 10
    assert numpy.isnan(result.loc["c"]["fish"])


def test_suppresses_all_equal_extra_values_to_reach_threshold():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {"fish": [1, 10, 10], "litres": [10, 10, 10]}, index=["a", "b", "c"]
    )
    result = calculate(m, data)

    assert numpy.isnan(result.loc["a"]["fish"])
    assert numpy.isnan(result.loc["b"]["fish"])
    assert numpy.isnan(result.loc["c"]["fish"])


def test_reports_suppression_of_small_values():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame({"fish": [1], "litres": [100]}, index=["bowl"])
    reporter = RecordingReporter()
    calculate(m, data, reporter)

    assert "Suppressed small numbers in column fish" in reporter.msg


def test_reports_suppression_of_extra_values():
    m = Measure(
        "ignored-id",
        numerator="fish",
        denominator="litres",
        small_number_suppression=True,
    )
    data = pandas.DataFrame(
        {"fish": [2, 10, 8], "litres": [10, 10, 10]}, index=["a", "b", "c"]
    )
    reporter = RecordingReporter()
    calculate(m, data, reporter)

    assert "Additional suppression in column fish" in reporter.msg


def calculate(measure, data, reporter=null_reporter):
    return measure.calculate(data, reporter)


@pytest.mark.parametrize(
    "measure_id,creates_new_combined_file,expected_output_file,expected_contents",
    [
        (
            # combines only date files with the measure id "test"; doesn't try to combine
            # others that start with "test", and ignores a file with an invalid date format
            "test",
            True,
            "measure_test.csv",
            [
                dict(a=1, b=2, c=3, date="2021-01-01"),
                dict(a=4, b=5, c=6, date="2021-02-01"),
                dict(a=7, b=8, c=9, date="2021-03-01"),
            ],
        ),
        (
            "test_code",
            True,
            "measure_test_code.csv",
            [
                dict(d=1, e=2, f=3, date="2021-03-01"),
                dict(d=4, e=5, f=6, date="2021-04-01"),
            ],
        ),
        (
            # No date stamped files; the existing non-date file remains unchanged (no date column added)
            "test_event",
            False,
            "measure_test_event.csv",
            [
                dict(a=0, b=0, c=0),
            ],
        ),
    ],
)
def test_csv_merging(
    tmpdir,
    measure_id,
    creates_new_combined_file,
    expected_output_file,
    expected_contents,
):
    fixtures_dir = Path(__file__).parent.absolute() / "fixtures" / "csv_date_merging"
    for file in fixtures_dir.iterdir():
        shutil.copy(file, tmpdir)
    output_dir = Path(tmpdir)
    output_file = output_dir / "measure_*.csv"

    assert (output_dir / expected_output_file).exists() is not creates_new_combined_file

    combine_csv_files_with_dates(output_file, measure_id)
    expected_file = output_dir / expected_output_file
    assert expected_file.exists()
    with open(expected_file) as infile:
        df = pandas.read_csv(infile)
        assert df.to_dict("records") == expected_contents


def test_csv_merging_with_mismtched_headers(tmpdir):
    fixtures_dir = Path(__file__).parent.absolute() / "fixtures" / "csv_date_merging"
    for file in fixtures_dir.iterdir():
        shutil.copy(file, tmpdir)
    output_dir = Path(tmpdir)
    output_file = output_dir / "measure_*.csv"

    assert not (output_dir / "measure_test_error.csv").exists()

    with pytest.raises(
        RuntimeError,
        match="Files .+/measure_test_error_2021-01-01.csv and .+/measure_test_error_2021-02-01.csv have different headers",
    ):
        combine_csv_files_with_dates(output_file, "test_error")
