# Diagrama entidad-relación (ERD)

Muestra las tablas, sus claves y las relaciones. Para el detalle de cada columna, ver [base_de_datos.md](base_de_datos.md) (diccionario de datos) y [`sql/create_schema.sql`](../sql/create_schema.sql); el porqué de cada decisión está en [decisiones.md](decisiones.md). Si cambia el esquema, hay que actualizar los cuatro.

Cómo leer las relaciones: `||--o{` significa "uno a muchos" (una fila de la tabla de la izquierda se relaciona con muchas de la derecha); `|o--o{`, lo mismo pero donde el lado "uno" es opcional.

```mermaid
erDiagram
    user ||--o{ plot : "tiene"
    plot ||--o{ sensor : "tiene"
    sensor ||--o{ sensor_reading : "produce"
    plot ||--o{ forecast_data : "recibe"
    plot ||--o{ historical_weather : "recibe"
    plot ||--o{ alert : "genera"
    plot ||--o{ recommendation : "recibe"
    plot ||--o{ report : "recibe"
    recommendation |o--o{ report : "sirve de base"

    user {
        bigint user_id PK
        varchar email UK "en minúsculas"
        varchar name
        varchar company_name "opcional"
        varchar phone "opcional"
        boolean is_active
        datetime created_at
    }

    plot {
        bigint plot_id PK
        bigint user_id FK
        varchar name
        decimal latitude
        decimal longitude
        decimal altitude_m
        varchar timezone
        datetime alerts_last_seen_at "marcador de alertas"
        varchar crop "pendiente"
        date cycle_start_date "pendiente"
        varchar growth_stage_manual "vacío = se calcula"
        decimal kc_manual "vacío = se calcula"
        decimal area_m2 "pendiente"
        varchar soil_texture "pendiente"
        decimal irrigation_efficiency "pendiente"
        decimal flow_rate_m3_h "pendiente"
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
        datetime measured_at
        decimal value "valor normalizado"
        boolean is_interpolated "marca SMMi"
        varchar quality_flag
        varchar quality_reason
        datetime received_at
    }

    forecast_data {
        bigint forecast_id PK
        bigint plot_id FK
        date fecha "día previsto"
        datetime captured_at "cuándo se descargó"
        tinyint lead_days "antelación"
        varchar source
        decimal tMax "y resto de columnas meteorológicas"
        decimal probPrecipitacion "solo previsión"
        decimal uvmax "solo previsión"
        smallint weather_code
    }

    historical_weather {
        bigint historical_id PK
        bigint plot_id FK
        date fecha "día observado"
        datetime downloaded_at
        varchar source
        decimal tMax "y resto de columnas meteorológicas"
        smallint weather_code
    }

    alert {
        bigint alert_id PK
        bigint plot_id FK
        varchar alert_type
        text description
        varchar status "active o resolved"
        datetime created_at
        datetime updated_at
        datetime resolved_at
    }

    recommendation {
        bigint recommendation_id PK
        bigint plot_id FK
        datetime calculated_at
        date period_start
        date period_end
        varchar status
        decimal net_dose_mm
        json reasons
        json inputs_snapshot
        json limitations
        varchar algorithm_version
    }

    report {
        bigint report_id PK
        bigint plot_id FK
        bigint recommendation_id FK "opcional"
        varchar report_type "daily o weekly"
        date period_start
        date period_end
        json content
        text text_es
        varchar generator "plantilla o LLM"
        varchar generator_version
    }

    job_run {
        bigint job_run_id PK
        varchar job_name
        varchar triggered_by
        datetime started_at
        datetime finished_at
        varchar status
        int plots_ok
        int plots_failed
        text error_summary
        json details
    }
```

## Notas

- `plot` es el centro del esquema: casi todo cuelga de ella. Como cada parcela pertenece a un único usuario, los permisos se comprueban con `plot.user_id`.
- `job_run` no se relaciona con las demás tablas: es un registro de la operación de las tareas.
- Unicidades: `forecast_data` (`plot_id`, `fecha`, `lead_days`, `source`), `historical_weather` (`plot_id`, `fecha`), `recommendation` (`plot_id`, `period_start`) y `report` (`plot_id`, `report_type`, `period_start`).
- Cada diagrama de Mermaid se dibuja directamente en GitHub y en muchos editores de Markdown.
