"""REST route registration (DD-3: routers are thin adapters over the service layer).

Each submodule owns one resource family's ``APIRouter`` and contains no business
logic of its own; ``register_routes`` is the single place that assembles them onto
the app so ``app.py`` stays a pure application factory.
"""

from __future__ import annotations

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    from glosswork.routes.admin_ops import router as admin_ops_router
    from glosswork.routes.agent_labels import router as agent_labels_router
    from glosswork.routes.attachments import router as attachments_router
    from glosswork.routes.audit import router as audit_router
    from glosswork.routes.auth import router as auth_router
    from glosswork.routes.bootstrap import router as bootstrap_router
    from glosswork.routes.changes import router as changes_router
    from glosswork.routes.comments import router as comments_router
    from glosswork.routes.csv import router as csv_router
    from glosswork.routes.identity import router as identity_router
    from glosswork.routes.operator_backup import router as operator_backup_router
    from glosswork.routes.records import router as records_router
    from glosswork.routes.saved_views import router as saved_views_router
    from glosswork.routes.schema import router as schema_router
    from glosswork.routes.search import router as search_router
    from glosswork.routes.search_index import router as search_index_router
    from glosswork.routes.usage import router as usage_router

    app.include_router(schema_router)
    app.include_router(records_router)
    app.include_router(comments_router)
    app.include_router(changes_router)
    app.include_router(csv_router)
    app.include_router(attachments_router)
    app.include_router(saved_views_router)
    app.include_router(audit_router)
    app.include_router(agent_labels_router)
    app.include_router(identity_router)
    app.include_router(auth_router)
    app.include_router(bootstrap_router)
    app.include_router(search_index_router)
    app.include_router(search_router)
    app.include_router(admin_ops_router)
    app.include_router(usage_router)
    app.include_router(operator_backup_router)
