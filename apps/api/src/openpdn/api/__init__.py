"""HTTP surface.

FastAPI lives here and nowhere else. Routes are thin: they translate HTTP into
an application-service call and the returned DTO into a response model. Any
route containing engineering logic is a layering defect (ADR-0001).
"""

from openpdn.api.app import create_app

__all__ = ["create_app"]
