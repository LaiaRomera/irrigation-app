"""Pruebas de la validación de cifras (7.1): una cifra distinta o inventada falla; las correctas pasan."""

from decimal import Decimal

from irrigation_app.reports.validation import AllowedFigures, extract_figures, validate_fields, validate_text


def allowed(**mm: float) -> AllowedFigures:
    figures = AllowedFigures()
    for value in mm.values():
        figures.add(value, "mm")
    return figures


def test_extracts_comma_and_point_decimals_with_units():
    figs = extract_figures("Regar 12,4 mm (o 12.4 mm) con 46 % de agotamiento a 25 cm en 3 días.")
    assert [(f.value, f.unit) for f in figs] == [
        (Decimal("12.4"), "mm"), (Decimal("12.4"), "mm"), (Decimal("46"), "%"), (Decimal("25"), "cm"),
        (Decimal("3"), "días"),
    ]


def test_correct_figures_pass_in_both_decimal_styles():
    figures = allowed(net=12.4)
    assert validate_text("Dosis neta de 12,4 mm.", figures).ok
    assert validate_text("Dosis neta de 12.4 mm.", figures).ok
    assert validate_text("Dosis neta de 12,40 mm.", figures).ok  # mismo valor


def test_rounded_figure_fails():
    result = validate_text("Dosis neta de 12 mm.", allowed(net=12.4))
    assert not result.ok
    assert "12 mm" in result.reason


def test_invented_figure_fails():
    result = validate_text("Regar 12,4 mm evita una pérdida de rendimiento del 15 %.", allowed(net=12.4))
    assert not result.ok
    assert "15 %" in result.reason


def test_unit_must_match():
    figures = allowed(net=12.4)
    figures.add(46, "%")
    assert validate_text("Se ha gastado el 46 %.", figures).ok
    assert not validate_text("Se ha gastado el 12,4 %.", figures).ok  # 12,4 es de mm, no de %
    assert not validate_text("Dosis de 46 mm.", figures).ok


def test_plain_numbers_may_match_any_engine_figure_but_not_new_ones():
    figures = allowed(net=12.4)
    figures.add(69.0)  # índice de humedad, sin unidad
    assert validate_text("Humedad media de 69,0 sobre 100.", figures).ok
    assert not validate_text("Humedad media de 70,0 sobre 100.", figures).ok


def test_days_of_the_plan_and_dates_are_allowed_only_if_given():
    figures = AllowedFigures()
    figures.add_all([25, 8, 2026])
    assert validate_text("El día 4, 25 de agosto de 2026.", figures).ok
    assert not validate_text("El día 12 de agosto.", figures).ok


def test_method_names_are_not_figures():
    assert validate_text("Método FAO-56 con ET₀ de Open-Meteo, datos de las 05:00 UTC.", AllowedFigures()).ok


def test_unknown_value_allows_nothing():
    figures = AllowedFigures()
    figures.add(None, "mm")
    assert not validate_text("Dosis de 0,5 mm.", figures).ok


def test_validate_fields_collects_problems_by_field():
    result = validate_fields({"summary": "Dosis 12,4 mm.", "reason": "Pérdida del 9 %.", "warning": "Sin cifras."}, allowed(net=12.4))
    assert not result.ok
    assert len(result.problems) == 1 and result.problems[0].startswith("reason")
