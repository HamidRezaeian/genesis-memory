# acme fixture — synthetic billing/notify package for EXP108 session tasks.
# Modules: store (KVStore, TTLCache), pricing (subtotal, apply_coupon, tax_total,
# format_receipt), api (register, handle_request, healthcheck), cli (render_table, main),
# notify (send, batch_send, outbox_len). Tests in tests/ pin 3 planted bugs (B1-B3).
