# Diagrama entidad-relación (ERD)

Muestra las tablas, sus claves y las relaciones. Para el detalle de cada columna, ver [base_de_datos.md](base_de_datos.md) (diccionario de datos) y [`sql/create_schema.sql`](../sql/create_schema.sql); el porqué de cada decisión está en [decisiones.md](decisiones.md). Si cambia el esquema, hay que actualizar los cuatro.

Cómo leer las relaciones: `||--o{` significa "uno a muchos" (una fila de la tabla de la izquierda se relaciona con muchas de la derecha); `|o--o{`, lo mismo pero donde el lado "uno" es opcional.

```mermaid
erDiagram
    app_user ||--o{ plot : "tiene"
    plot ||--o{ sensor : "tiene"
    sensor ||--o{ sensor_reading : "produce"
    plot ||--o{ forecast_data : "recibe"
    plot ||--o{ historical_weather : "recibe"
    plot ||--o{ alert : "genera"
    plot ||--o{ recommendation : "recibe"
    recommendation |o--o{ recommendation : "sustituye a"
    recommendation ||--o{ report : "sirve de base"
    knowledge_document ||--o{ knowledge_chunk : "se divide en"

    app_user {
        bigint user_id PK
        varchar email UK "en minúsculas"
        varchar name
        varchar company_name "opcional"
        varchar phone "opcional"
        boolean is_active
        timestamptz created_at
    }

    plot {
        bigint plot_id PK
        bigint user_id FK
        varchar name
        numeric latitude
        numeric longitude
        numeric altitude_m
        varchar timezone
        timestamptz alerts_last_seen_at "marcador de alertas"
        varchar crop "pendiente"
        date cycle_start_date "pendiente"
        varchar growth_stage_manual "vacío = se calcula"
        numeric kc_manual "vacío = se calcula"
        numeric area_m2 "pendiente"
        varchar soil_texture "pendiente"
        numeric irrigation_efficiency "pendiente"
        numeric flow_rate_m3_h "pendiente"
    }

    sensor {
        bigint sensor_id PK
        bigint plot_id FK
        smallint depth_cm
        boolean is_active
        varchar external_code "pendiente"
    }

    sensor_reading {
        bigint reading_id PK
        bigint sensor_id FK
        timestamptz measured_at
        numeric value "valor normalizado"
        boolean is_interpolated "marca SMMi"
        varchar quality_flag
        varchar quality_reason
        timestamptz received_at
    }

    forecast_data {
        bigint forecast_id PK
        bigint plot_id FK
        date fecha "día previsto"
        timestamptz captured_at "cuándo se descargó"
        smallint lead_days "antelación"
        varchar source
        numeric tmax "y resto de columnas meteorológicas"
        numeric probprecipitacion "solo previsión"
        numeric uvmax "solo previsión"
        smallint weather_code
    }

    historical_weather {
        bigint historical_id PK
        bigint plot_id FK
        date fecha "día observado"
        timestamptz downloaded_at
        varchar source
        numeric tmax "y resto de columnas meteorológicas"
        smallint weather_code
    }

    alert {
        bigint alert_id PK
        bigint plot_id FK
        varchar alert_type
        text description
        varchar status "active o resolved"
        timestamptz created_at
        timestamptz updated_at
        timestamptz resolved_at
    }

    recommendation {
        bigint recommendation_id PK
        bigint plot_id FK
        bigint supersedes_id FK "versión anterior"
        date period_start
        date period_end
        int version
        timestamptz calculated_at
        varchar decision "qué se recomienda"
        varchar validation_status "confianza en datos y método"
        jsonb daily_plan "plan de 7 días"
        jsonb reasons
        jsonb inputs_snapshot
        jsonb limitations
        boolean needs_review
        varchar method_version
        varchar parameter_set_version
    }

    report {
        bigint report_id PK
        bigint recommendation_id FK
        varchar report_type "daily o weekly"
        timestamptz generated_at
        jsonb content
        text text_es
        varchar generator "plantilla o LLM"
        varchar generator_version
        jsonb validation_checks
    }

    job_run {
        bigint job_run_id PK
        varchar job_name
        varchar triggered_by
        timestamptz started_at
        timestamptz finished_at
        varchar status
        int plots_ok
        int plots_failed
        text error_summary
        jsonb details
    }

    knowledge_document {
        bigint document_id PK
        varchar title
        varchar organization
        text source_url
        date published_at
        varchar license
        varchar crop
        varchar region
    }

    knowledge_chunk {
        bigint chunk_id PK
        bigint document_id FK
        int chunk_index
        text content
        varchar section
        varchar page_ref
        vector embedding "dimensión por decidir"
        varchar embedding_model
    }
```

## Notas

- `plot` es el centro del esquema: casi todo cuelga de ella. Como cada parcela pertenece a un único usuario, los permisos se comprueban con `plot.user_id`.
- `job_run` no se relaciona con las demás tablas: es un registro de la operación de las tareas.
- `recommendation` es inmutable (un disparador impide modificarla o borrarla). Un recálculo crea otra fila con `version` + 1 que apunta a la anterior con `supersedes_id`.
- `report` solo se relaciona con `recommendation`: de ella obtiene la parcela y el periodo.
- Las tablas del RAG (`knowledge_document`, `knowledge_chunk`) son independientes del resto: no cuelgan de ninguna parcela.
- Unicidades: `forecast_data` (`plot_id`, `fecha`, `lead_days`, `source`), `historical_weather` (`plot_id`, `fecha`), `recommendation` (`plot_id`, `period_start`, `version`) y `report` (`recommendation_id`, `report_type`, `generator`, `generator_version`).
- Las columnas meteorológicas se escriben en minúsculas en la base (`tmax`, `rhmedia`...), aunque en la documentación conserven las mayúsculas de la empresa.
- Cada diagrama de Mermaid se dibuja directamente en GitHub y en muchos editores de Markdown.
