"""Validación por código de las cifras de un texto del LLM contra las del motor (tarea 7.1).

Regla (decisiones.md, «Redacción del informe»): cada cifra y unidad del texto debe coincidir con una del
motor o de los datos que se le pasaron al LLM, y no pueden aparecer cifras nuevas. Si falla, el informe usa
la plantilla de respaldo.

Cómo se comprueba:
- Se extraen del texto todos los números (coma o punto decimal) con su unidad si la llevan (`mm`, `%`, `cm`,
  `m³`, `°C`, `puntos`, `días`).
- Un número con unidad debe estar entre las cifras permitidas de **esa** unidad (12 mm no vale si el motor
  dio 12,4 mm; 12,4 % tampoco vale si 12,4 es de milímetros).
- Un número sin unidad (fechas, días del plan, índice de humedad) debe estar entre las cifras permitidas de
  cualquier tipo.
- La comparación es exacta, en valor: 17,60 es 17,6, pero 18 no es 17,6.

Límites conocidos (se declaran, no se esconden): un número escrito con letras («doce milímetros») no se
detecta, y un número con separador de miles («1.234») se lee como decimal. El prompt pide cifras con dígitos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

# Textos fijos que contienen números y no son cifras del informe (nombre del método, hora de origen...)
_IGNORED = re.compile(r"FAO[-\s]?56|ET\s?[0₀]|\b05:00\b", re.IGNORECASE)

_UNIT_ALIASES = {
    "mm": "mm", "%": "%", "cm": "cm", "m³": "m3", "m3": "m3", "°c": "°C", "ºc": "°C",
    "punto": "puntos", "puntos": "puntos", "día": "días", "días": "días", "dia": "días", "dias": "días",
}
_FIGURE = re.compile(
    r"(?<![\w.,])(?P<num>\d+(?:[.,]\d+)?)(?:\s?(?P<unit>mm|%|cm|m³|m3|°C|ºC|puntos?|días?|dias?)(?![\wáéíóú]))?",
    re.IGNORECASE,
)
# Cifras que siempre pueden aparecer sin unidad: los días del plan y los extremos de la escala del sensor
_ALWAYS_ALLOWED_PLAIN = tuple(range(0, 8)) + (100,)


@dataclass(frozen=True)
class Figure:
    """Una cifra encontrada en un texto."""

    value: Decimal
    unit: str  # "" si no lleva unidad
    text: str  # tal como aparece en el texto


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


def extract_figures(text: str) -> list[Figure]:
    """Todas las cifras del texto, con su unidad (normalizada) si la llevan."""
    cleaned = _IGNORED.sub(" ", text)
    out: list[Figure] = []
    for m in _FIGURE.finditer(cleaned):
        value = _to_decimal(m.group("num"))
        if value is None:
            continue
        unit = m.group("unit")
        out.append(Figure(value, _UNIT_ALIASES[unit.lower()] if unit else "", m.group(0).strip()))
    return out


@dataclass
class AllowedFigures:
    """Las cifras que el texto puede usar: por unidad y sin unidad."""

    by_unit: dict[str, set[Decimal]] = field(default_factory=dict)
    plain: set[Decimal] = field(default_factory=lambda: {Decimal(n) for n in _ALWAYS_ALLOWED_PLAIN})

    def add(self, value: float | int | Decimal | None, unit: str = "") -> None:
        """Permite una cifra del motor. Un valor desconocido (None) no permite nada."""
        if value is None:
            return
        number = value if isinstance(value, Decimal) else Decimal(str(value))
        if unit:
            self.by_unit.setdefault(_UNIT_ALIASES.get(unit.lower(), unit), set()).add(number)
        else:
            self.plain.add(number)

    def add_all(self, values: Iterable[float | int | None], unit: str = "") -> None:
        for v in values:
            self.add(v, unit)

    def add_text(self, text: str) -> None:
        """Permite todas las cifras de un texto que ya viene del motor (por ejemplo, el mensaje de una limitación)."""
        for fig in extract_figures(text):
            self.add(fig.value, fig.unit)

    def allows(self, figure: Figure) -> bool:
        if figure.unit:
            return figure.value in self.by_unit.get(figure.unit, set())
        if figure.value in self.plain:
            return True
        return any(figure.value in values for values in self.by_unit.values())


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    problems: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        """Motivo breve para registrar cuando falla."""
        return "; ".join(self.problems)


def validate_text(text: str, allowed: AllowedFigures, *, label: str = "texto") -> ValidationResult:
    """Comprueba un texto. Falla si alguna cifra o su unidad no está entre las permitidas."""
    problems = [
        f"{label}: la cifra «{fig.text}» no coincide con ninguna del motor"
        for fig in extract_figures(text)
        if not allowed.allows(fig)
    ]
    return ValidationResult(ok=not problems, problems=tuple(problems))


def validate_fields(texts: Mapping[str, str], allowed: AllowedFigures) -> ValidationResult:
    """Comprueba varios campos (`summary`, `reason`, `warning`) y junta los problemas."""
    problems: list[str] = []
    for name, text in texts.items():
        problems.extend(validate_text(text, allowed, label=name).problems)
    return ValidationResult(ok=not problems, problems=tuple(problems))
