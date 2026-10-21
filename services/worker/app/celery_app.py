from dr_core.tasks import create_celery_app

celery_app = create_celery_app(
    include=[
        "app.tasks.health",
        "app.tasks.research",
        "app.tasks.storage_smoke",
    ]
)
