from contextlib import closing
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from werkzeug.security import check_password_hash, generate_password_hash

AUTHORIZED = 'Estás autorizado'
UNAUTHORIZED = 'Por favor revise sus credenciales que no se encuentra autorizado'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    apellido TEXT NOT NULL,
    usuario TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    titulo TEXT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT '',
    realizada INTEGER NOT NULL DEFAULT 0 CHECK (realizada IN (0, 1))
);
CREATE INDEX IF NOT EXISTS tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS tokens_user_id ON tokens(user_id);
'''


class TextBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('*', mode='before')
    @classmethod
    def validate_text(cls, value, info):
        if not isinstance(value, str):
            raise ValueError('El campo debe ser texto')
        if info.field_name != 'descripcion' and not value.strip():
            raise ValueError('El campo no puede estar vacío')
        return value if info.field_name == 'contrasena' else value.strip()


class LoginBody(TextBody):
    usuario: str = Field(max_length=256)
    contrasena: str = Field(max_length=256)


class RegisterBody(LoginBody):
    nombre: str = Field(max_length=256)
    apellido: str = Field(max_length=256)


class TaskCreateBody(TextBody):
    titulo: str = Field(max_length=256)
    descripcion: str = Field(default='', max_length=5000)


class TaskUpdateBody(TaskCreateBody):
    descripcion: str = Field(max_length=5000)


def create_app(database=None):
    app = FastAPI(title='API de tareas', version='1.0.0')
    database_path = str(database or os.environ.get('DATABASE_PATH', Path(__file__).parent / 'data' / 'tasks.sqlite3'))
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as db:
        db.executescript(SCHEMA)

    def get_db():
        with closing(sqlite3.connect(database_path, check_same_thread=False)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys = ON')
            yield db

    bearer = HTTPBearer(auto_error=False)

    def protected(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
        if credentials is None:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)
        token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        token = db.execute('SELECT user_id FROM tokens WHERE token_hash = ? AND expires_at > ?', (token_hash, int(time.time()))).fetchone()
        if token is None:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)
        return token['user_id']

    def task_json(row):
        return {'id': row['id'], 'titulo': row['titulo'], 'descripcion': row['descripcion'], 'realizada': bool(row['realizada'])}

    def find_task(task_id, db, user_id):
        return db.execute('SELECT * FROM tasks WHERE id = ? AND user_id = ?', (task_id, user_id)).fetchone()

    @app.post('/register', status_code=201)
    def register(payload: RegisterBody, db=Depends(get_db)):
        data = payload.model_dump()
        if len(data['contrasena']) < 8:
            raise ValueError('La contraseña debe tener al menos 8 caracteres')
        try:
            with db:
                cursor = db.execute('INSERT INTO users (nombre, apellido, usuario, password_hash) VALUES (?, ?, ?, ?)', (data['nombre'], data['apellido'], data['usuario'], generate_password_hash(data['contrasena'])))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail='El usuario ya existe')
        return dict(message='Usuario creado', id=cursor.lastrowid)

    @app.post('/login')
    def login(payload: LoginBody, db=Depends(get_db)):
        data = payload.model_dump()
        user = db.execute('SELECT * FROM users WHERE usuario = ?', (data['usuario'],)).fetchone()
        if user is None or not check_password_hash(user['password_hash'], data['contrasena']):
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 3600
        with db:
            db.execute('DELETE FROM tokens WHERE expires_at <= ?', (int(time.time()),))
            db.execute('INSERT INTO tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)', (user['id'], hashlib.sha256(token.encode()).hexdigest(), expires_at))
        return dict(message=AUTHORIZED, token=token, token_type='Bearer', expires_at=expires_at)
    
    

    @app.post('/tasks', status_code=201)
    def create_task(payload: TaskCreateBody, user_id=Depends(protected), db=Depends(get_db)):
        data = payload.model_dump()
        with db:
            cursor = db.execute('INSERT INTO tasks (user_id, titulo, descripcion) VALUES (?, ?, ?)', (user_id, data['titulo'], data.get('descripcion', '')))
        return dict(message=AUTHORIZED, task=task_json(find_task(cursor.lastrowid, db, user_id)))

    @app.get('/tasks')
    def list_tasks(user_id=Depends(protected), db=Depends(get_db)):
        rows = db.execute('SELECT * FROM tasks WHERE user_id = ? ORDER BY id', (user_id,)).fetchall()
        return dict(message=AUTHORIZED, tasks=[task_json(row) for row in rows])

    @app.get('/tasks/{task_id}')
    def get_task(task_id: int, user_id=Depends(protected), db=Depends(get_db)):
        task = find_task(task_id, db, user_id)
        if task is None:
            raise HTTPException(status_code=404, detail='Tarea no encontrada')
        return dict(message=AUTHORIZED, task=task_json(task))

    @app.put('/tasks/{task_id}')
    def update_task(task_id: int, payload: TaskUpdateBody, user_id=Depends(protected), db=Depends(get_db)):
        if find_task(task_id, db, user_id) is None:
            raise HTTPException(status_code=404, detail='Tarea no encontrada')
        data = payload.model_dump()
        with db:
            db.execute('UPDATE tasks SET titulo = ?, descripcion = ? WHERE id = ? AND user_id = ?', (data['titulo'], data['descripcion'], task_id, user_id))
        return dict(message=AUTHORIZED, task=task_json(find_task(task_id, db, user_id)))

    def set_completed(task_id, completed, db, user_id):
        if find_task(task_id, db, user_id) is None:
            raise HTTPException(status_code=404, detail='Tarea no encontrada')
        with db:
            db.execute('UPDATE tasks SET realizada = ? WHERE id = ? AND user_id = ?', (completed, task_id, user_id))
        return dict(message=AUTHORIZED, task=task_json(find_task(task_id, db, user_id)))

    @app.put('/tasks/{task_id}/complete')
    def complete_task(task_id: int, user_id=Depends(protected), db=Depends(get_db)):
        return set_completed(task_id, 1, db, user_id)

    @app.put('/tasks/{task_id}/uncomplete')
    def uncomplete_task(task_id: int, user_id=Depends(protected), db=Depends(get_db)):
        return set_completed(task_id, 0, db, user_id)

    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=5000)
