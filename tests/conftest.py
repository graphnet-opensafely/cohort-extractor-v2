import docker
import docker.errors
import pytest

from databuilder.definition.base import cohort_registry
from databuilder.dsl import Cohort
from databuilder.query_engines.mssql import MssqlQueryEngine
from databuilder.query_engines.spark import SparkQueryEngine

from .lib.databases import make_database, make_spark_database, wait_for_database
from .lib.docker import Containers
from .lib.frames import events, registrations
from .lib.mock_backend import MockPatients, backend_factory
from .lib.util import extract


# We want to automatically apply the `integration` mark to any test that uses Docker. That is what the single, ignored
# param to the fixture achieves.
#
# Amazingly this is the only way of adding marks to tests based on their use of a fixture. The behaviour we need is
# documented here: https://docs.pytest.org/en/6.2.x/fixture.html#parametrizing-fixtures. The misuse of this behaviour
# in this way is _almost_ condoned by the pytest developers here: https://github.com/pytest-dev/pytest/issues/1368.
# (And if the feature requested by that issue is ever implement it we can use it instead.)
#
# This replaces an earlier approach where we explicitly applied marks to all the integration tests and then _asserted_
# in the fixture that it was being used by an integration test. Unfortunately that doesn't work if the fixture is
# session-scoped (because, reasonably, you can't access the `request` object that represents an initial test inside a
# session-scoped fixture).
@pytest.fixture(
    scope="session", params=[pytest.param(0, marks=pytest.mark.integration)]
)
def docker_client():
    yield docker.from_env()


@pytest.fixture(scope="session")
def containers(docker_client):
    yield Containers(docker_client)


@pytest.fixture(scope="session")
def database(containers):
    database = make_database(containers)
    wait_for_database(database)
    yield database


@pytest.fixture(scope="session")
def spark_database(containers):
    database = make_spark_database(containers)
    wait_for_database(database, timeout=15)
    yield database


@pytest.fixture(autouse=True)
def cleanup_register():
    yield
    cohort_registry.reset()


@pytest.fixture
def cohort_with_population():
    cohort = Cohort()
    cohort.set_population(registrations.exists_for_patient())
    yield cohort


class QueryEngineFixture:
    def __init__(self, name, database, query_engine_class):
        self.name = name
        self.database = database
        self.query_engine_class = query_engine_class
        self.backend = backend_factory(query_engine_class)

    def setup(self, *items):
        return self.database.setup(*items)

    def extract(self, cohort, **kwargs):
        results = extract(cohort, self.backend, self.database, **kwargs)
        # We don't explicitly order the results and not all databases naturally return
        # in the same order
        results.sort(key=lambda i: i["patient_id"])
        return results

    def sqlalchemy_engine(self, **kwargs):
        return self.database.engine(
            dialect=self.query_engine_class.sqlalchemy_dialect, **kwargs
        )


@pytest.fixture(
    scope="session", params=["mssql", pytest.param("spark", marks=pytest.mark.spark)]
)
def engine(request, database, spark_database):
    name = request.param
    if name == "mssql":
        return QueryEngineFixture(name, database, MssqlQueryEngine)
    elif name == "spark":
        return QueryEngineFixture(name, spark_database, SparkQueryEngine)
    else:
        assert False


@pytest.fixture
def bool_series():
    return lambda: events.exists_for_patient()


@pytest.fixture
def code_series():
    return (
        lambda: events.sort_by(events.code)
        .first_for_patient()
        .select_column(events.code)
    )


@pytest.fixture
def date_series():
    return (
        lambda: events.sort_by(events.date)
        .first_for_patient()
        .select_column(events.date)
    )


@pytest.fixture
def int_series():
    return (
        lambda: events.sort_by(events.value)
        .first_for_patient()
        .select_column(events.value)
    )


@pytest.fixture
def patient_series():
    patients = MockPatients()
    return lambda: patients.select_column(patients.sex)
