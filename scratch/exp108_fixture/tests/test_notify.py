import acme.notify as N


def _flaky_factory(fail_times):
    state = {"n": 0}

    def transport(to, msg):
        state["n"] += 1
        return state["n"] > fail_times

    return transport, state


def test_send_retries_until_success():
    N.OUTBOX.clear()
    tr, st = _flaky_factory(2)
    assert N.send("a@x.io", "hi", retries=3, transport=tr) == 3
    assert st["n"] == 3


def test_send_returns_attempts():
    N.OUTBOX.clear()
    tr, st = _flaky_factory(0)
    assert N.send("a@x.io", "hi", retries=3, transport=tr) == 1
