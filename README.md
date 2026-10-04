# API de tareas con FastAPI y SQLite

## Iniciar

Dependencias instaladas en `.venv`. Desde esta carpeta:

```sh
source .venv/bin/activate
python app.py
```

API local: http://127.0.0.1:5000. La base se crea automáticamente en `data/tasks.sqlite3`. Para cambiar su ubicación, define `DATABASE_PATH`. La API se ejecuta con Uvicorn para desarrollo local.

Documentación interactiva: http://127.0.0.1:5000/docs. Ejecuta `/login` con usuario y contraseña, copia el token y pégalo en **Authorize** para probar las rutas protegidas.

También puedes iniciar con recarga automática:

```sh
.venv/bin/uvicorn app:create_app --factory --host 127.0.0.1 --port 5000 --reload
```

Se usa Werkzeug únicamente para mantener compatibles los hashes de contraseñas existentes; el framework HTTP es FastAPI.

Para instalar en otro entorno:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Rutas

| Método | Ruta | Uso |
| --- | --- | --- |
| POST | `/register` | Crear usuario |
| POST | `/login` | Validar usuario y contraseña; retornar token |
| POST | `/tasks` | Crear tarea y retornar su ID generado |
| GET | `/tasks` | Listar tareas del usuario, cada una con ID |
| GET | `/tasks/<id>` | Obtener una tarea |
| PUT | `/tasks/<id>` | Reemplazar título y descripción |
| PUT | `/tasks/<id>/complete` | Marcar realizada |
| PUT | `/tasks/<id>/uncomplete` | Desmarcar realizada |

Todas las rutas `/tasks` requieren el header `Authorization: Bearer <token>`.
Las respuestas exitosas incluyen `message: "Estás autorizado"`. Un token ausente, inválido o vencido devuelve HTTP 401 con `message: "Por favor revise sus credenciales que no se encuentra autorizado"`. El login con credenciales incorrectas devuelve el mismo mensaje. Una tarea inexistente o ajena devuelve 404.

## Ejemplo

Crear usuario (contraseña de al menos 8 caracteres):

```sh
curl -X POST http://127.0.0.1:5000/register \
  -H 'Content-Type: application/json' \
  -d '{"nombre":"Ana","apellido":"Pérez","usuario":"ana","contrasena":"mi-clave-123"}'
```

Obtener token:

```sh
curl -X POST http://127.0.0.1:5000/login \
  -H 'Content-Type: application/json' \
  -d '{"usuario":"ana","contrasena":"mi-clave-123"}'
```

Copiar el campo `token` de la respuesta:

```sh
TOKEN='pega-aqui-el-token'
curl -X POST http://127.0.0.1:5000/tasks \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"titulo":"Estudiar Python","descripcion":"Practicar una hora"}'

curl http://127.0.0.1:5000/tasks -H "Authorization: Bearer $TOKEN"

# Sustituye 1 por el ID que retornó la creación.
curl http://127.0.0.1:5000/tasks/1 -H "Authorization: Bearer $TOKEN"
curl -X PUT http://127.0.0.1:5000/tasks/1 \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"titulo":"Practicar SQLite","descripcion":"Crear una tabla"}'
curl -X PUT http://127.0.0.1:5000/tasks/1/complete -H "Authorization: Bearer $TOKEN"
curl -X PUT http://127.0.0.1:5000/tasks/1/uncomplete -H "Authorization: Bearer $TOKEN"
```

`titulo` es obligatorio. En creación, `descripcion` es opcional; en modificación es obligatoria, pero permite texto vacío. Los campos de texto admiten hasta 256 caracteres y la descripción hasta 5000. Los nombres de usuario y los datos de login distinguen mayúsculas y minúsculas. Los cuerpos deben ser JSON; los campos desconocidos se rechazan.

## Base de datos

- `users`: ID, nombre, apellido, usuario único y hash de contraseña (scrypt).
- `tokens`: ID, `user_id` como foreign key a `users.id`, hash SHA-256 del token y vencimiento.
- `tasks`: ID, `user_id` como foreign key a `users.id`, título, descripción y estado realizada.

Los tokens son aleatorios, duran una hora (3600 segundos) y se entregan en texto únicamente al iniciar sesión. La base guarda su hash. Las consultas usan parámetros y verifican el propietario de la tarea. Las foreign keys están habilitadas en cada conexión de la API.

El gestor de consola SQLite ya está instalado en el equipo:

```sh
sqlite3 data/tasks.sqlite3
.tables
.schema
.quit
```

## Pruebas

```sh
.venv/bin/python -m unittest -v
```

Cubren creación, consulta, edición, marcado y desmarcado, autorización en todas las rutas de tareas, aislamiento entre usuarios, validación, hashes, vencimiento, persistencia y foreign keys. Usan una base temporal.
