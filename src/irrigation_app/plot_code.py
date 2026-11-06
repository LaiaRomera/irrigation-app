"""Código de parcela (`plot.plot_code`): aleatorio, no secuencial y sin caracteres ambiguos.

Es lo que el usuario escribe o busca en su lista de parcelas. **No da acceso por sí solo**: el servidor
comprueba siempre que la parcela es de la cuenta con sesión iniciada.
"""

from __future__ import annotations

import re
import secrets

# Sin 0, 1, I, L ni O, que se confunden al leerlos o teclearlos: 31 símbolos.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LENGTH = 8
PATTERN = re.compile(f"^[{ALPHABET}]{{{LENGTH}}}$")  # el mismo que el CHECK de la base


def generate_plot_code() -> str:
    """8 símbolos del alfabeto elegidos con `secrets` (unos 8,5 · 10^11 combinaciones)."""
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def is_valid_plot_code(code: str) -> bool:
    return bool(PATTERN.match(code))
