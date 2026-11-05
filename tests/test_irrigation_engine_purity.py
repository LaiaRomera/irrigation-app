"""El motor es una función pura: determinista y sin red ni base de datos."""

import ast
import json
import subprocess
import sys
from pathlib import Path

from irrigation_app.irrigation_engine import EngineResult, run_engine

from irrigation_engine_factory import CROSS_ON_DAY_4, engine_input, predictions

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "irrigation_app" / "irrigation_engine"
FORBIDDEN = {
    "requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "socket", "ssl", "psycopg", "psycopg2", "sqlalchemy",
    "sqlite3", "dotenv", "streamlit", "pandas", "irrigation_app.db", "irrigation_app.weather",
}


def test_same_inputs_give_the_same_serialized_result():
    first = run_engine(engine_input(CROSS_ON_DAY_4)).model_dump_json()
    for _ in range(3):
        assert run_engine(engine_input(CROSS_ON_DAY_4)).model_dump_json() == first


def test_the_order_of_the_predictions_does_not_change_the_result():
    preds = predictions(CROSS_ON_DAY_4)
    a = run_engine(engine_input(None, preds=preds)).model_dump_json()
    b = run_engine(engine_input(None, preds=list(reversed(preds)))).model_dump_json()
    assert a == b


def test_result_survives_a_json_round_trip():
    result = run_engine(engine_input(CROSS_ON_DAY_4))
    again = EngineResult.model_validate_json(result.model_dump_json())
    assert again == result and json.loads(result.model_dump_json())["plan"][3]["decision"] == "irrigate"


def test_engine_does_not_read_the_clock():
    sources = "".join(p.read_text(encoding="utf-8") for p in PACKAGE.glob("*.py"))
    assert "today(" not in sources and "datetime.now" not in sources and "time.time" not in sources


def test_no_module_of_the_package_imports_network_or_database_code():
    imported = set()
    for path in [*PACKAGE.glob("*.py"), PACKAGE.parent / "reference_plot.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    bad = {name for name in imported if name in FORBIDDEN or name.split(".")[0] in FORBIDDEN}
    assert not bad, f"el motor no puede importar red ni base de datos: {sorted(bad)}"


def test_importing_the_package_loads_no_network_or_database_module():
    code = (
        "import sys, json; sys.path.insert(0, 'src'); import irrigation_app.irrigation_engine as e; "
        "e.run_engine; print(json.dumps(sorted(m for m in sys.modules "
        "if m.split('.')[0] in %r)))" % sorted({"requests", "urllib3", "httpx", "psycopg", "sqlalchemy", "socket"})
    )
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=root, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []
