# Spec Delta

## Purpose

Dar al agrónomo una web sencilla en la que entrar con su cuenta, ver solo sus parcelas, pedir el informe de riego del día y descargarlo en PDF, sin que la web descargue datos de proveedores ni entrene modelos.

## ADDED Requirements

### Requirement: Acceso con cuenta precargada
La web SHALL exigir login y SHALL permitir el acceso solo a los usuarios activos de la tabla `app_user`, comparando el email del login. Sin registro público.

#### Scenario: Email desconocido
- **WHEN** alguien entra con un email que no está en `app_user` o está inactivo
- **THEN** la web no muestra ninguna parcela y explica que la cuenta no tiene acceso

### Requirement: Solo parcelas propias
La web SHALL mostrar únicamente las parcelas del usuario, y cada lectura de datos, recomendación, informe o PDF SHALL comprobar el permiso en el servidor.

#### Scenario: Parcela ajena
- **WHEN** un usuario pide el informe de una parcela que no es suya (por ejemplo, cambiando el identificador)
- **THEN** el sistema lo rechaza sin revelar datos de esa parcela

### Requirement: Abrir la web no recalcula
Abrir la web SHALL limitarse a leer de la base de datos: sin llamar a Open-Meteo ni al LLM, sin entrenar y sin ejecutar el motor.

#### Scenario: Resumen de parcelas
- **WHEN** el usuario abre la web
- **THEN** ve sus parcelas con la fecha del último informe y de los datos que usó

### Requirement: Solicitar el informe de hoy
Desde el detalle de una parcela, el usuario SHALL poder solicitar el informe de hoy, que ejecuta el motor con los datos guardados y genera el informe.

#### Scenario: Informe solicitado
- **WHEN** el usuario pulsa «solicitar informe de hoy»
- **THEN** se crea una recomendación nueva y su informe, y la página lo muestra con la fecha de la recomendación y un botón para descargar el PDF

#### Scenario: Informe antiguo
- **WHEN** hoy no se ha pedido ningún informe de la parcela
- **THEN** la página muestra el último con su fecha bien visible, junto al botón de solicitar el de hoy, y nunca lo presenta como actual

### Requirement: Alertas agronómicas de la parcela
La web SHALL mostrar las alertas activas de cada parcela del usuario (por ejemplo, lluvia fuerte prevista o suelo muy seco), generadas por la tarea diaria y no al abrir la web. Este requisito es recortable si el plazo aprieta.

#### Scenario: Alerta activa
- **WHEN** la tarea diaria ha creado una alerta activa para una parcela del usuario
- **THEN** el detalle de la parcela la muestra con su descripción, sin ejecutar ningún cálculo al abrirlo

### Requirement: Errores sin detalles internos
Si falla la generación, la web SHALL mostrar un mensaje en español sin claves, rutas ni trazas, y el último resultado disponible con su fecha.

#### Scenario: Falla el informe
- **WHEN** la generación del informe lanza un error
- **THEN** el usuario ve un aviso genérico y el último informe disponible, y el error queda registrado sin secretos
