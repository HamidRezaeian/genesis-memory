"""Minimal request router."""
ROUTES = {}


def register(route, fn):
    ROUTES[route] = fn


def handle_request(path, params=None):
    fn = ROUTES.get(path)
    if fn is None:
        return {"status": 404, "body": "not found"}
    return {"status": 200, "body": fn(params or {})}


def healthcheck():
    return {"status": 200, "body": "ok"}
