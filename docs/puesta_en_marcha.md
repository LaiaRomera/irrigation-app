# Puesta en marcha

Cómo preparar el entorno de desarrollo desde cero. Los comandos están escritos para **bash** (por ejemplo, Git Bash en Windows). Todos se ejecutan desde la raíz del proyecto, salvo que se indique otra cosa.

Estado: solo están implementados el esquema de la base de datos y su script de aplicación. La web y las tareas aún no existen.

## 1. Requisitos

- **Python 3.14.** El proyecto se ha desarrollado con la 3.14.3; no se ha probado con otras versiones.
- **Una base de datos PostgreSQL 17 con la extensión pgvector**, de una de estas dos formas:
  - **Aiven** (plan gratuito), que es la base de desarrollo en la nube. Necesita el certificado CA del servicio (`ca.pem`).
  - **Docker**, para un PostgreSQL local de pruebas (imagen `pgvector/pgvector:pg17`).
- **Una cuenta gratuita de Auth0**, solo para probar el login.

## 2. Entorno de Python

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

Las versiones están fijadas en `requirements.txt` a propósito. `streamlit[auth]` y `Authlib` en concreto se fijan porque, según el foro de Streamlit, actualizar sin control rompió el login con Auth0 en Streamlit Community Cloud.

## 3. Configuración de la base de datos

La conexión se lee de un archivo `.env`, que **nunca se sube a git**. La plantilla es `.env.example`:

| Variable | Qué es |
|---|---|
| `DB_HOST` | Servidor |
| `DB_PORT` | Puerto |
| `DB_USER` | Usuario |
| `DB_PASSWORD` | Contraseña |
| `DB_NAME` | Debe ser `irrigation_app` (con guion bajo) |
| `DB_SSL_CA` | Ruta al certificado CA si el servidor exige SSL (Aiven); vacío en Docker local |

1. Copia `.env.example` a `.env`.
2. Rellena los valores del panel de tu servicio de Aiven.
3. Descarga el certificado CA del servicio y guárdalo como `ca.pem` en la raíz del proyecto. Con `DB_SSL_CA=ca.pem`, la ruta se interpreta desde la raíz. `*.pem` está en `.gitignore`.

**Cuidado con la base:** el servicio de Aiven puede alojar otras bases. Este proyecto solo debe usar `irrigation_app`. El script de la sección 5 se niega a ejecutarse si `DB_NAME` es otro nombre.

Para otro destino (por ejemplo, el PostgreSQL local) puedes crear otro archivo, como `.env.docker-pg`; los archivos `.env.*` están ignorados por git, salvo `.env.example`.

### 3.1. Crear el servicio PostgreSQL en Aiven

1. En la consola de Aiven: **Create service → PostgreSQL** y elige el plan **Free**. Solo se permite un servicio gratuito de cada tipo.
2. Cuando esté en marcha, copia del panel del servicio el host, el puerto, el usuario y la contraseña a tu `.env`. Descarga el certificado CA como `ca.pem`.
3. La base `irrigation_app` no hace falta crearla a mano: `scripts/apply_schema.py` la crea si no existe (conectando antes a la base de mantenimiento del servicio, `defaultdb`).
4. La extensión pgvector está disponible en el plan gratuito; el propio SQL del esquema la activa (`CREATE EXTENSION vector`).

5. En el `.env`, `DB_NAME` es `irrigation_app` aunque el panel muestre `defaultdb` (esa es la base de mantenimiento que trae Aiven).
6. Un servicio recién creado tarda un rato en publicar su nombre de host: si da "failed to resolve host", espera a que el estado sea *Running*.

El script verifica el SSL con `verify-ca` (que el certificado lo firme la CA de Aiven). Con `verify-full` la librería de Windows falla con "certificate contains IP address with invalid length 16". Además, libpq no abre el certificado si la ruta completa lleva caracteres no ASCII (como la "ó" de `Programación`), por lo que el script trabaja desde la raíz del proyecto con la ruta relativa.

No pegues contraseñas en el chat ni en archivos que se suban a git.

## 4. PostgreSQL local con Docker (opcional)

Sirve para pruebas que escriben datos sin gastar el almacenamiento gratuito de Aiven (1 GB). Requiere Docker Desktop en marcha.

```bash
docker run -d --name irrigation-pg -e POSTGRES_PASSWORD="<contraseña>" -e POSTGRES_DB=irrigation_app -p 127.0.0.1:5433:5432 -v irrigation_pg_data:/var/lib/postgresql/data pgvector/pgvector:pg17
```

- La imagen `pgvector/pgvector:pg17` es PostgreSQL 17 con pgvector ya instalado (unos 640 MB).
- El puerto `5433` evita chocar con un PostgreSQL local en el `5432`, y se publica solo en `127.0.0.1`.
- La contraseña es de pruebas; ponla en un archivo `.env.docker-pg` con `DB_HOST=127.0.0.1`, `DB_PORT=5433`, `DB_USER=postgres`, `DB_NAME=irrigation_app` y `DB_SSL_CA` vacío.
- Tarda unos segundos en aceptar conexiones la primera vez.

Parar y volver a arrancar (los datos se conservan en el volumen):

```bash
docker stop irrigation-pg
```

```bash
docker start irrigation-pg
```

Para borrarlo todo, incluidos los datos (irreversible):

```bash
docker rm -f irrigation-pg
```

```bash
docker volume rm irrigation_pg_data
```

El contenedor `agrodevices-mysql-test` es de MySQL y pertenece al proyecto antiguo: no es de esta aplicación y no se toca.

## 5. Crear el esquema

```bash
.venv/Scripts/python scripts/apply_schema.py
```

Con otro archivo de configuración:

```bash
.venv/Scripts/python scripts/apply_schema.py .env.docker-pg
```

Qué hace y qué garantías da:
1. Se niega a ejecutarse si `DB_NAME` no es `irrigation_app`.
2. Crea la base `irrigation_app` si no existe, en UTF-8.
3. **Si la base ya tiene tablas, no hace nada**, para no pisar datos.
4. Si está vacía, aplica `sql/create_schema.sql` (incluida la extensión pgvector) y muestra el recuento de tablas, claves foráneas, `CHECK` y unicidades, y la versión de pgvector.
5. Nunca imprime la contraseña.

Resultado esperado: `Esquema aplicado: 12 tablas, 10 claves foráneas, 13 CHECK y 8 UNIQUE.` y `Extensión vector: 0.8.7` (la versión puede variar).

## 6. Probar el login con Auth0

La prueba comprueba que un usuario puede entrar con email y contraseña a una app de Streamlit y que la app recibe su email. Se hace en local y es desechable.

### 6.1. En el panel de Auth0

1. Crea una cuenta gratuita en auth0.com (no pide tarjeta).
2. **Applications → Applications → Create Application**, de tipo **Regular Web Applications**.
3. En la pestaña **Settings** de la aplicación:
   - *Allowed Callback URLs:* `http://localhost:8501/oauth2callback`
   - *Allowed Logout URLs:* `http://localhost:8501`
   - Guarda los cambios.
4. **User Management → Users → Create User:** un usuario de prueba con email y contraseña.
5. Recomendable: en **Authentication → Database → Username-Password-Authentication → Settings**, activa **Disable Sign Ups**, para que nadie pueda registrarse por su cuenta (cuentas precargadas).
6. Anota el *Domain*, el *Client ID* y el *Client Secret* (pestaña Settings de la aplicación). **El Client Secret es un secreto: no lo compartas ni lo subas a git.**

Los nombres exactos de los menús de Auth0 pueden variar.

### 6.2. Una app mínima

En una carpeta temporal que git ignore (por ejemplo, `data/login_test/`), crea `app.py`:

```python
import streamlit as st

st.title("Prueba de login")

if not st.user.is_logged_in:
    st.write("No has iniciado sesión.")
    if st.button("Iniciar sesión"):
        st.login("auth0")
else:
    st.success("Sesión iniciada")
    st.write(f"Email: {st.user.email}")
    st.write(f"Nombre: {st.user.name}")
    if st.button("Cerrar sesión"):
        st.logout()
```

Y en la misma carpeta, `.streamlit/secrets.toml`:

```toml
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "una-cadena-larga-y-aleatoria"

[auth.auth0]
client_id = "tu-client-id"
client_secret = "tu-client-secret"
server_metadata_url = "https://TU-DOMINIO/.well-known/openid-configuration"
```

- `cookie_secret`: una cadena aleatoria larga, por ejemplo la salida de `openssl rand -hex 32`.
- `TU-DOMINIO` es el *Domain* de Auth0, **sin** `https://` delante (ese prefijo ya está escrito).
- **Todos los valores van entre comillas.** Sin ellas el archivo es inválido y Streamlit lo ignora en silencio.

### 6.3. Lanzar la prueba

**Desde dentro de la carpeta de la app**, porque Streamlit busca `.streamlit/secrets.toml` en la carpeta desde la que se lanza, no donde está `app.py`:

```bash
cd data/login_test
```

```bash
../../.venv/Scripts/python -m streamlit run app.py
```

Se abre `http://localhost:8501`. En la primera ejecución Streamlit pregunta por un correo: puedes dejarlo vacío con Enter. Pulsa *Iniciar sesión*, entra con el usuario de prueba, y deberías volver a la app viendo el email y el nombre.

### 6.4. Después de la prueba

Resultado de la prueba del 8 de octubre de 2026: **funciona en local**. Falta probarlo en Streamlit Community Cloud (ver [arquitectura.md](arquitectura.md), sección 9, y [decisiones.md](decisiones.md)).

Al terminar, se borra la carpeta `data/login_test/`.

## 7. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `st.user has no attribute "is_logged_in"` | Streamlit no lee el `secrets.toml`: se lanzó desde otra carpeta, o el archivo es inválido | Lanzar desde la carpeta que contiene `.streamlit/`, y comprobar que los valores llevan comillas |
| Error de *callback mismatch* en Auth0 | La URL de *Allowed Callback URLs* no coincide exactamente | Debe ser `http://localhost:8501/oauth2callback`, sin espacios ni barra final |
| Streamlit arranca en el puerto 8502 | Hay otra instancia en marcha | Pararla con `Ctrl+C`; Auth0 solo acepta el 8501 |
| `DB_NAME vale '...' y debe ser 'irrigation_app'` | El `.env` apunta a otra base | Corregir la línea `DB_NAME` |
| `La base ya tiene N tablas` | El esquema ya está aplicado | No hace falta hacer nada |
| `No se pudo conectar` | Servidor apagado (Aiven lo apaga por inactividad; en local, Docker Desktop parado o contenedor detenido), datos incorrectos o falta el `ca.pem` | Encender el servicio desde su panel (o `docker start irrigation-pg`) y revisar `.env` |
| `command not found` al escribir un nombre de archivo | Un nombre de archivo no es un comando | Abrirlo desde el editor (`Ctrl+P`) o con `code <ruta>` |
