from ..contracts import tables
from ..query_engines.mssql import MssqlQueryEngine
from .base import BaseBackend, Column, MappedTable, QueryTable


def rtrim(ref, char):
    """
    Right trim arbitrary characters.

    MSSQL's {L,R}TRIM() functions (unlike TRIM()) don't allow trimming of
    arbitrary characters, only spaces. So we've implemented it here. Note that
    this won't work as-is for '^' or ']', but they could be supported with a
    bit of escaping.

      * Since we're using PATINDEX(), which only works from the front of the
        string, we REVERSE() the string every time we refer to it and then
        re-REVERSE() the result once we're done.
      * We search through the string to find the index of the first character
        that isn't the thing we're stripping.
      * Then we work out the length of the string remaining from that point to
        the end.
      * Finally we take a substring from the index to the end of the string.

    (LTRIM() could be implemented in the same way, but without all the reversing.)
    """
    assert len(char) == 1
    assert char not in ["^", "]"]
    return f"""
        REVERSE(
            SUBSTRING(
                REVERSE({ref}),
                PATINDEX(
                    '%[^{char}]%',
                    REVERSE({ref})
                ),
                LEN({ref}) - PATINDEX('%[^{char}]%', REVERSE({ref})) + 1
            )
        )
    """


def string_split(ref, delim):
    """
    Split strings on the given delimiter.

    The compatibility level that TPP runs SQL Server at doesn't include the
    `STRING_SPLIT()` function, so we implement it here ourselves. This use of
    SQL Server's XML-handling capabilities is truly ugly, but it allows us to
    implement this inline so we don't need to define a function.

    Implementation copied from
    https://sqlperformance.com/2012/07/t-sql-queries/split-strings.
    """
    return f"""
        (
            SELECT Value = y.i.value('(./text())[1]', 'nvarchar(4000)')
            FROM
            (
                SELECT x = CONVERT(
                    XML,
                    '<i>' + REPLACE({ref}, '{delim}', '</i><i>') + '</i>'
                ).query('.')
            ) AS t CROSS APPLY x.nodes('i') AS y(i)
        )
    """


class TPPBackend(BaseBackend):
    """Backend for working with data in TPP."""

    backend_id = "tpp"
    query_engine_class = MssqlQueryEngine
    patient_join_column = "Patient_ID"

    patients = MappedTable(
        source="Patient",
        columns=dict(
            sex=Column("varchar", source="Sex"),
            date_of_birth=Column("date", source="DateOfBirth"),
        ),
    )

    patient_demographics = QueryTable(
        implements=tables.PatientDemographics,
        columns=dict(
            sex=Column("varchar"),
            date_of_birth=Column("date"),
            date_of_death=Column("date"),
        ),
        query="""
            SELECT  Patient_ID as patient_id,
                DateOfBirth as date_of_birth,
                CASE
                    WHEN DateOfDeath = '9999-12-31' THEN NULL
                    ELSE
                        datefromparts(year(DateOfDeath), month(DateOfDeath), 1)
                    END as date_of_death,
                CASE
                    WHEN Sex = 'M' THEN 'male'
                    WHEN Sex = 'F' THEN 'female'
                    WHEN Sex = 'I' THEN 'intersex'
                    ELSE 'unknown'
                END as sex
            FROM Patient
        """,
    )

    clinical_events = QueryTable(
        columns=dict(
            code=Column("varchar"),
            system=Column("varchar"),
            date=Column("datetime"),
            numeric_value=Column("float"),
        ),
        query="""
            SELECT Patient_ID as patient_id, CTV3Code as code, 'ctv3' as system, ConsultationDate AS date, NumericValue AS numeric_value FROM CodedEvent
            UNION ALL
            SELECT Patient_ID as patient_id, ConceptID as code, 'snomed' as system, ConsultationDate AS date, NumericValue AS numeric_value FROM CodedEvent_SNOMED
        """,
    )

    practice_registrations = QueryTable(
        columns=dict(
            pseudo_id=Column("integer"),
            nuts1_region_name=Column("varchar"),
            date_start=Column("datetime"),
            date_end=Column("datetime"),
        ),
        query="""
            SELECT RegistrationHistory.Patient_ID AS patient_id,
                RegistrationHistory.StartDate AS date_start,
                RegistrationHistory.EndDate AS date_end,
                Organisation.Organisation_ID AS pseudo_id,
                Organisation.Region as nuts1_region_name
            FROM RegistrationHistory
            LEFT OUTER JOIN Organisation ON RegistrationHistory.Organisation_ID = Organisation.Organisation_ID
        """,
    )

    sgss_sars_cov_2 = QueryTable(
        columns=dict(
            date=Column("date"),
            positive_result=Column("boolean"),
        ),
        query="""
            SELECT Patient_ID as patient_id, Specimen_Date AS date, 1 AS positive_result FROM SGSS_AllTests_Positive
            UNION ALL
            SELECT Patient_ID as patient_id, Specimen_Date AS date, 0 AS positive_result FROM SGSS_AllTests_Negative
        """,
    )

    hospitalizations = QueryTable(
        columns=dict(
            date=Column("date"),
            code=Column("varchar"),
            system=Column("varchar"),
        ),
        query=f"""
            SELECT Patient_ID as patient_id, Admission_Date as date, {rtrim("fully_split.Value", "X")} as code, 'icd10' as system
            FROM APCS
            -- Our string_split() implementation only works as long as the codelists do not contain '<', '>' or '&'
            -- characters. If that assumption is broken then this will fail unpredictably.
            CROSS APPLY {string_split("Der_Diagnosis_All", " ||")} pipe_split
            CROSS APPLY {string_split("pipe_split.Value", " ,")} fully_split
        """,
    )

    patient_address = QueryTable(
        columns=dict(
            patientaddress_id=Column("integer"),
            date_start=Column("date"),
            date_end=Column("date"),
            index_of_multiple_deprivation_rounded=Column("integer"),
            has_postcode=Column("boolean"),
        ),
        query="""
            SELECT
              Patient_ID as patient_id,
              PatientAddress_ID as patientaddress_id,
              StartDate as date_start,
              EndDate as date_end,
              ImdRankRounded as index_of_multiple_deprivation_rounded,
              IIF(MSOACode = 'NPC', 0, 1) as has_postcode
            FROM PatientAddress
        """,
    )
