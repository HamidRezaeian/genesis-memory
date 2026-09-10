"""Notification outbox with retry."""
OUTBOX = []


def send(to, msg, retries=3, transport=None):
    """Attempt delivery up to `retries` times. Returns attempts used."""
    transport = transport or (lambda t, m: True)
    attempts = 0
    # BUG B2 (planted): tries only once regardless of `retries`.
    for _ in range(1):
        attempts += 1
        if transport(to, msg):
            OUTBOX.append((to, msg))
            return attempts
    OUTBOX.append((to, msg))
    return attempts


def batch_send(jobs, **kw):
    return [send(to, msg, **kw) for to, msg in jobs]


def outbox_len():
    return len(OUTBOX)
