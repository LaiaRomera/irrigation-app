# Informe de riego (`reports/`)

Convierte el resultado del motor (`EngineResult`, contrato C2) en un informe diario o semanal con texto en español, gráficas y PDF. Ninguna cifra del informe sale de otro sitio que el motor (y, para el error típico, las métricas del modelo).

## Flujo

```
EngineResult ──► content.build_report_content ──► llm.generate_text ──► render.render_report_html ──► pdf.print_pdf
                  (cifras del motor,                (LLM o plantilla,      (plantilla Jinja2 +          (Chromium)
                   error típico de                   cifras validadas)      gráficas Matplotlib)
                   metrics.py)
```

| Módulo | Qué hace |
|---|---|
| `content.py` | `ReportContent`: lo que muestra el informe (decisión, primer día, rango de riego, limitaciones, error típico). `figures()` da las cifras que un texto puede citar. |
| `metrics.py` | Error típico por horizonte, de `data/ml/metrics.csv` (LightGBM, prueba, todas las unidades). Sin archivo: lista vacía, el informe lo dice. |
| `validation.py` | Extrae cifras y unidades del texto y las compara con las permitidas. Cifra distinta, nueva o con otra unidad: falla. |
| `llm.py` | `TemplateGenerator` (respaldo), `LlmGenerator` + `LlmClient` abstracto, `FakeLlmClient` (pruebas) y `generate_text` (LLM, validación y respaldo con motivo). Prompt en `prompts/agronomist_v1.md`. |
| `charts.py` | Humedad prevista y umbral, plan de 7 días (dosis y lluvia) y error por horizonte, como PNG. |
| `render.py` | Rellena la plantilla HTML con Jinja2. |
| `pdf.py` | Imprime el HTML a PDF con Chromium (Playwright o un Edge/Chrome instalado). |
| `service.py` | `generate_report(plot_id, user_id, report_type)`: une la base de datos, el motor, el texto y el guardado. Ver abajo. |

## La plantilla de Agrodevices (punto de sustitución)

La plantilla real es un documento interno y **no está en el repositorio**. `render.py` la busca en la carpeta de `AGRODEVICES_TEMPLATE_DIR` (archivo `index.html`). Si no está, usa `templates/informe_minimo.html` (propia, mínima) y el resultado lo declara (`template_source == "builtin_minimal"`).

Para usar la real hay que convertir sus campos en marcas Jinja (`{{ ... }}`) en una copia **local**. Si la plantilla no tiene marcas, `render.py` avisa en `template_note`. Variables disponibles:

- `c`: el `ReportContent` (`c.plot_label`, `c.origin_date`, `c.decision_label`, `c.validation_status`, `c.indicative`, `c.first_day.*`, `c.other_days`, `c.outlook`, `c.typical_error`, `c.limitations`, `c.is_stale`, `c.uncertainty_note`...).
- `text`: `summary`, `reason`, `warning`, `generator`, `generator_version`.
- `charts`: `moisture`, `plan` (solo semanal) y `error`, como `data:` URI (o `None`).
- Filtros: `num` (coma decimal, `| num(2)`), `date_es` y `stage_es`.

## Reglas que cumple

- El LLM no escribe HTML (se rechaza el texto con etiquetas) ni busca en internet. Redacta `summary`, `reason` y `warning`.
- Si no hay cliente, falla la llamada, falta la estructura o una cifra no coincide, el texto es el de la plantilla de respaldo y se guarda el motivo en `fallback_reason` (códigos cortos, sin claves ni rutas).
- Siempre se muestra «INDICATIVA» y, con `insufficient_data`, «DATOS INSUFICIENTES». Un informe de un día anterior al de la consulta lleva un aviso con su fecha.
- El día de cruce del umbral nunca se da como exacto: se muestra el rango (anclajes ±10 %) y que decide la lectura real de la sonda.
- Los mm de la dosis son sobre la franja mojada; no hay duración.

## Límites conocidos de la validación

Un número escrito con letras («doce») no se detecta, y «1.234» se lee como decimal. El prompt pide cifras con dígitos. La comparación es exacta: 18 mm no vale si el motor dio 17,6 mm.

## PDF

Con Playwright: `pip install playwright` y `python -m playwright install chromium` (no se instalan solos). Sin Playwright se usa el Edge o Chrome ya instalado (variable `PDF_BROWSER_PATH` para forzar uno). Ejemplos: `python tests/fixtures/make_sample_reports.py` (salida en `data/reports_examples/`).

## Servicio (`service.py`, tarea 7.4)

`generate_report(plot_id, user_id, report_type, *, connection=None, origin_date=None, today=None, text_generator=None)` es lo único que llama la web. Devuelve un `ReportOutcome` con `status`:

| `status` | Cuándo | Qué trae |
|---|---|---|
| `generated` | parcela de referencia (1168) con predicciones | `report` (contenido, texto, `report_id`, `recommendation_id`, si es antiguo) |
| `crop_data_unavailable` | cualquier otra parcela: sin cultivo, suelo, riego ni anclajes | mensaje claro y la humedad prevista por sonda (`moisture_forecast`); **no hay recomendación de riego ni se guarda nada** |
| `no_prediction` | la parcela no tiene predicciones en `ml_prediction` | mensaje; la web no calcula nada |

- **Permiso**: la parcela debe ser del usuario (y ambos activos). Si no, `AccessDenied` con el mismo mensaje exista o no. El `plot_code` solo sirve para elegir en la lista del usuario (`find_user_plot`, `list_user_plots`).
- **Entradas del motor**: `ml_prediction` del último origen, `forecast_data` (origen+1 a origen+7; por fecha se prefiere la antelación `fecha − origen`) y `plot.irrigation_interval_days` como intervalo de reposición (nulo: el motor supone 3 días). Lo que falta es `None`, nunca una cifra supuesta.
- **Recomendación**: se reutiliza si ya existe una con las mismas entradas y la misma versión del método; si no, se crea una versión nueva enlazada (`supersedes_id`). Dos informes (LLM y plantilla) de la misma recomendación comparten `recommendation_id`.
- **Informe**: único por recomendación, tipo, generador y versión; repetir la llamada devuelve el guardado (`created=False`). `content` se guarda sin `today`; la fecha de consulta se aplica al leer, así un informe de un día anterior sale marcado como antiguo.
- **Texto**: `text_generator=None` usa la plantilla (`fallback_reason = no_llm_configured`). El proveedor real del LLM se enchufa aquí cuando se elija (0.4).
- **PDF**: `build_report_pdf(connection, report_id, user_id)` lo genera al pedirlo desde el informe guardado (no se guarda).
- **Errores**: `ReportError` con código y mensaje fijo (sin claves, rutas ni trazas). Los errores de la base de datos se convierten en `database_error`.
- `tests/fixtures/seed_reference_origin.py` prepara en el Docker local un día de la 1168 (predicción con los modelos activos y previsión archivada) y genera los informes.

## Cómo adaptar la plantilla de Agrodevices (propuesta, sin copiarla al repositorio)

La plantilla (`03_Plantilla_informe.pptx`) es una presentación 16:9 de 17 diapositivas, no un HTML. Solo se ha leído para tomar su estructura y estilo; no se ha copiado nada. Sus campos van entre llaves simples (`{sector.parcela}`, `{dia_1.temp_max}`, `{conclusion_1_texto}`...) y su estilo es: titulares en Georgia y texto en Calibri; verdes oscuros para los fondos y los titulares, verde medio como acento, ámbar para los avisos y rojo para el riesgo; etiquetas pequeñas en mayúsculas con numeración («01 · PROPÓSITO», «08 · PREVISIÓN»).

Propuesta: crear **fuera del repositorio** una carpeta con `index.html` (A4 vertical, o apaisado si se quiere conservar el aspecto de diapositiva) que use esa tipografía, la misma paleta (sacada de los colores de la presentación) y estas correspondencias, y apuntar a ella con `AGRODEVICES_TEMPLATE_DIR`:

| Diapositiva de Agrodevices | Parte del informe | Variables Jinja |
|---|---|---|
| Portada (cliente, localidad, periodo) | Cabecera: parcela, fecha del cálculo, tipo | `c.plot_label`, `c.origin_date`, `c.report_type` |
| Contexto de la parcela (cultivo, riego, estado) | Ficha de la parcela y sus supuestos | `c.plot_label`, `c.limitations`, `c.replenishment_interval_days` |
| Previsión 7 días y recomendación de riego | Primer día en detalle y los otros seis resumidos | `c.first_day.*`, `c.other_days`, `charts.plan` |
| Análisis por profundidad | Humedad prevista y umbral | `charts.moisture` |
| Diagnóstico y cuadro de mando | Decisión, estado de validación y marca de indicativa | `c.decision_label`, `c.validation_label`, `c.indicative_note` |
| Conclusiones (cuatro bloques) | Resumen, motivo, aviso y error típico | `text.summary`, `text.reason`, `text.warning`, `c.typical_error`, `charts.error` |

Pasos: (1) Laia confirma el formato (A4 vertical frente a apaisado) y qué diapositivas importan; (2) se escribe el `index.html` con esas marcas Jinja en una carpeta local; (3) `render.py` ya lo usa si existe y avisa si no tiene marcas. Lo que no aparece en la plantilla (error típico, marca de indicativa, advertencias, fecha de un informe antiguo) hay que añadirlo en un bloque propio: es obligatorio en todos los informes.
