import pytest

from backend.app.core.config import settings


@pytest.fixture(autouse=True)
def isolated_document_memory(tmp_path, monkeypatch):
    # Tests must never read or mutate the running application's knowledge base.
    monkeypatch.setattr(settings, "document_memory_path", tmp_path / "document-memory")
