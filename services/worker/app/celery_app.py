from dr_core.db.schema import log_embedding_dimension_mismatch
from dr_core.tasks import create_celery_app

log_embedding_dimension_mismatch()

celery_app = create_celery_app(
    include=[
        "app.tasks.health",
        "app.tasks.parse",
        "app.tasks.research",
        "app.tasks.storage_smoke",
    ]
)
