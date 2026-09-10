from acme.pricing import tax_total


def test_tax_rounds_standard():
    assert tax_total(10.00, 0.09) == 10.90
    assert tax_total(19.99, 0.09) == 21.79
    assert tax_total(0.10, 0.20) == 0.12
