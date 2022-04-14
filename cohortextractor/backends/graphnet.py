from cohortextractor.backends.base import BaseBackend, Column, MappedTable
from cohortextractor.query_engines.mssql import MssqlQueryEngine


class GraphnetBackend(BaseBackend):
    backend_id = "graphnet"
    query_engine_class = MssqlQueryEngine
    patient_join_column = "Patient_ID"

    patients = MappedTable(
        schema='TRE',
        source="Patients",
        columns=dict(
            sex=Column("varchar", source="Sex"),
            date_of_birth=Column("date", source="DateOfBirth"),
            date_of_death=Column("date", source="DateOfDeath"),
        ),
    )

    clinical_events = MappedTable(
        schema='TRE',
        source="ClinicalEvents",
        columns=dict(
            code=Column("varchar", source="Code"),
            system=Column("varchar", source="CodingSystem"),
            date=Column("datetime", source="ConsultationDate"),
            numeric_value=Column("float", source="NumericValue"),
            care_setting=Column("varchar", source="CareSetting"),
        ),
    )

    practice_registrations = MappedTable(
        schema='TRE',
        source="PracticeRegistrations",
        columns=dict(
            pseudo_id=Column("integer", source="Organisation_ID"),
            nuts1_region_name=Column("varchar", source="Region"),
            date_start=Column("datetime", source="StartDate"),
            date_end=Column("datetime", source="EndDate"),
        ),
    )

    covid_test_results = MappedTable(
        schema='TRE',
        source="CovidTestResults",
        columns=dict(
            date=Column("date", source="SpecimenDate"),
            positive_result=Column("boolean", source="positive_result"),
        ),
    )

    hospitalizations = MappedTable(
        schema='TRE',
        source="Hospitalisations",
        columns=dict(
            date=Column("date", source="AdmitDate"),
            code=Column("varchar", source="DiagCode"),
        ),
    )

    patient_address = MappedTable(
        schema='TRE',
        source="PatientAddresses",
        columns=dict(
            patientaddress_id=Column("integer", source="PatientAddress_ID"),
            date_start=Column("date", source="StartDate"),
            date_end=Column("date", source="EndDate"),
            index_of_multiple_deprivation_rounded=Column("integer", source="IMD"),
            msoa_code=Column("varchar", source="MSOACode"),
            has_postcode=Column("boolean", source="has_postcode"),
        ),
    )
