from acme.store import TTLCache


def test_expire_keeps_fresh():
    c = TTLCache(ttl_seconds=60.0, clock=lambda: 1000.0)
    c.set("k", "v")
    assert c.expire("k") is False
    assert c.get("k") == "v"


def test_expire_drops_stale():
    now = [1000.0]
    c = TTLCache(ttl_seconds=60.0, clock=lambda: now[0])
    c.set("k", "v")
    now[0] = 2000.0
    assert c.expire("k") is True
    assert c.get("k") is None
