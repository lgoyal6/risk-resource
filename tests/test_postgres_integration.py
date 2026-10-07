import os

import pytest

from risk_resource.adapters.postgres import PostgresRepository
from risk_resource.sample import demo_scenario

pytestmark = pytest.mark.integration


@pytest.fixture
def repository():
    dsn = os.getenv("RR_POSTGRES_DSN")
    if not dsn:
        pytest.skip("RR_POSTGRES_DSN is not configured")
    repo = PostgresRepository(dsn)
    repo.open()
    repo.migrate()
    yield repo
    repo.close()


def test_scenario_round_trip(repository):
    scenario = demo_scenario()
    repository.save_scenario(scenario, "demo")
    loaded = repository.get_scenario(scenario.id, "demo")
    assert loaded == scenario
    assert repository.get_scenario(scenario.id, "other") is None
