from app.core.config import get_settings
from app.services.embedding_service import EmbeddingService


def test_local_hash_embedding_shape_and_stability():
    settings = get_settings()
    service = EmbeddingService()

    v1 = service.embed_query("retry policy for payment worker")
    v2 = service.embed_query("retry policy for payment worker")

    assert len(v1) == settings.embedding_dimensions
    assert v1 == v2
