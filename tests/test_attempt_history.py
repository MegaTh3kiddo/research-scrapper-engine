from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from research_engine.application.recovery import RecoveryService
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.recovery import SqlAlchemyRecoveryStore


def test_attempt_history_includes_pre_request_rejections_and_supports_paging(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'history.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    query_id = str(uuid4())
    at = datetime(2026, 9, 26, tzinfo=timezone.utc)
    try:
        with sessions.begin() as session:
            for index, state in enumerate(("failed", "skipped", "running")):
                session.add(m.ExecutionAttempt(
                    requested_query_id=query_id, requested_provider="pubmed",
                    started_at=at + timedelta(seconds=index), state=state,
                    error_code="UnsupportedFiltersError" if state == "failed" else None,
                ))
            session.add(m.ExecutionAttempt(requested_query_id="unknown", requested_provider="unknown",
                                           started_at=at, state="failed", error_code="KeyError"))
        service = RecoveryService(SqlAlchemyRecoveryStore(sessions))
        history = service.list_attempts(query_id)
        assert [item.state for item in history] == ["running", "skipped", "failed"]
        assert history[-1].run_id is None
        assert history[-1].error_code == "UnsupportedFiltersError"
        assert service.list_attempts(query_id, limit=1, offset=1) == (history[1],)
        assert len(service.list_attempts()) == 4
        with pytest.raises(ValueError):
            service.list_attempts(query_id, limit=0)
        with pytest.raises(ValueError):
            service.list_attempts(query_id, offset=-1)
    finally:
        engine.dispose()
