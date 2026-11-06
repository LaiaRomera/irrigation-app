-- Migración 002: dos columnas nuevas en plot (código de parcela e intervalo de riego).
--
-- Se aplica con
--     .venv/Scripts/python scripts/apply_schema.py <archivo .env> --migration sql/migrations/002_plot_code_and_interval.sql
-- Para una base nueva, las mismas sentencias están al final de sql/create_schema.sql (una prueba
-- comprueba que coinciden). Necesita la tabla plot.

-- Código con el que el usuario escribe o busca una parcela en su lista. Aleatorio (generado con
-- el módulo secrets, no secuencial) y sin caracteres ambiguos (sin 0, 1, I, L ni O): 8 de 31
-- símbolos. NO da acceso por sí solo: el servidor comprueba siempre que la parcela es de la
-- cuenta con sesión iniciada. Vacío en las parcelas anteriores a esta migración.
ALTER TABLE plot ADD COLUMN plot_code VARCHAR(8) UNIQUE
    CONSTRAINT chk_plot_code_format CHECK (plot_code ~ '^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$');

-- Cada cuántos días riega la parcela (1 a 7). El motor lo recibe como
-- replenishment_interval_days; vacío = supuesto de 3 días.
ALTER TABLE plot ADD COLUMN irrigation_interval_days SMALLINT
    CONSTRAINT chk_plot_irrigation_interval CHECK (irrigation_interval_days BETWEEN 1 AND 7);
