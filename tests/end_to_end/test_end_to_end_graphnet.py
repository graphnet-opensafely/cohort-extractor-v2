import pytest

from ..lib.graphnet_schema import ClinicalEvents, Patients, PracticeRegistrations
from .utils import assert_results_equivalent


@pytest.mark.smoke
def test_extracts_data_from_sql_server_smoke_test(
    load_study, database, cohort_extractor_in_container
):
    run_test(load_study, database, cohort_extractor_in_container)


@pytest.mark.integration
def test_extracts_data_from_sql_server_integration_test(
    load_study, database, cohort_extractor_in_process
):
    run_test(load_study, database, cohort_extractor_in_process)


def run_test(load_study, database, cohort_extractor, dummy_data_file=None):
    database.setup(
        Patients(Patient_ID=1, DateOfBirth="1980-01-01"),
        ClinicalEvents(
            Patient_ID=1, ConsultationDate="2021-01-01", Code="xyz", CodingSystem="CTV3"
        ),
        PracticeRegistrations(Patient_ID=1),
        Patients(Patient_ID=2, DateOfBirth="1948-02-02"),
        ClinicalEvents(
            Patient_ID=2, ConsultationDate="2021-02-02", Code="abc", CodingSystem="CTV3"
        ),
        PracticeRegistrations(Patient_ID=2),
    )
    study = load_study(
        "end_to_end_tests_graphnet",
        dummy_data_file=dummy_data_file,
        definition_file="cohort_graphnet.py",
    )
    actual_results = cohort_extractor(
        study, backend="graphnet", use_dummy_data=dummy_data_file is not None
    )
    assert_results_equivalent(actual_results, study.expected_results())


def test_dummy_data(load_study, cohort_extractor_in_process_no_database):
    study = load_study(
        "end_to_end_tests_graphnet",
        definition_file="cohort_graphnet.py",
    )
    actual_results = cohort_extractor_in_process_no_database(study, use_dummy_data=True)
    assert_results_equivalent(actual_results, study.expected_results())


@pytest.mark.integration
def test_extracts_data_from_sql_server_ignores_dummy_data_file(
    load_study, database, cohort_extractor_in_process
):
    # A dummy data file is ignored if running in a real backend (i.e. DATABASE_URL is set)
    # This provides an invalid dummy data file, but it is ignored so no errors are raised
    run_test(
        load_study,
        database,
        cohort_extractor_in_process,
        "invalid_dummy_data.csv",
    )
