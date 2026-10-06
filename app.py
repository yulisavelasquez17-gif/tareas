import hashlib
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from database import engine as default_engine, metadata, tasks, tokens, users

AUTHORIZED = 'Estás autorizado'
UNAUTHORIZED = 'Por favor revise sus credenciales que no se encuentra autorizado'


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

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request, error):
        return JSONResponse(status_code=400, content={'message': 'Solicitud inválida'})

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request, error):
        return JSONResponse(status_code=error.status_code, content={'message': error.detail})

    @app.exception_handler(ValueError)
    async def value_error_handler(request, error):
        return JSONResponse(status_code=400, content={'message': str(error)})

    if database is None:
        app_engine = default_engine
    else:
        database_path = Path(database)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        app_engine = create_engine(
            f'sqlite:///{database_path}',
            connect_args={'check_same_thread': False},
        )

    if app_engine.dialect.name == 'sqlite':
        @event.listens_for(app_engine, 'connect')
        def enable_sqlite_foreign_keys(connection, connection_record):
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys = ON')
            cursor.close()

    metadata.create_all(app_engine)

    def get_db():
        with app_engine.connect() as db:
            yield db

    bearer = HTTPBearer(auto_error=False)

    def protected(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
        if credentials is None:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)
        token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        query = select(tokens.c.user_id).where(
            tokens.c.token_hash == token_hash,
            tokens.c.expires_at > int(time.time()),
        )
        token = db.execute(query).mappings().first()
        if token is None:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)
        return token['user_id']

    def task_json(row):
        return {'id': row['id'], 'titulo': row['titulo'], 'descripcion': row['descripcion'], 'realizada': bool(row['realizada'])}

    def find_task(task_id, db, user_id):
        query = select(tasks).where(
            tasks.c.id == task_id,
            tasks.c.user_id == user_id,
        )
        return db.execute(query).mappings().first()

    @app.post('/register', status_code=201)
    def register(payload: RegisterBody, db=Depends(get_db)):
        data = payload.model_dump()
        if len(data['contrasena']) < 8:
            raise ValueError('La contraseña debe tener al menos 8 caracteres')
        try:
            query = insert(users).values(
                nombre=data['nombre'],
                apellido=data['apellido'],
                usuario=data['usuario'],
                password_hash=generate_password_hash(data['contrasena']),
            )
            result = db.execute(query)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail='El usuario ya existe')
        return dict(message='Usuario creado', id=result.inserted_primary_key[0])

    @app.post('/login')
    def login(payload: LoginBody, db=Depends(get_db)):
        data = payload.model_dump()
        query = select(users).where(users.c.usuario == data['usuario'])
        user = db.execute(query).mappings().first()
        if user is None or not check_password_hash(user['password_hash'], data['contrasena']):
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 3600
        db.execute(delete(tokens).where(tokens.c.expires_at <= int(time.time())))
        db.execute(insert(tokens).values(
            user_id=user['id'],
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=expires_at,
        ))
        db.commit()
        return dict(message=AUTHORIZED, token=token, token_type='Bearer', expires_at=expires_at)
    
    

    @app.post('/tasks', status_code=201)
    def create_task(payload: TaskCreateBody, user_id=Depends(protected), db=Depends(get_db)):
        data = payload.model_dump()
        query = insert(tasks).values(
            user_id=user_id,
            titulo=data['titulo'],
            descripcion=data.get('descripcion', ''),
        )
        result = db.execute(query)
        db.commit()
        task_id = result.inserted_primary_key[0]
        return dict(message=AUTHORIZED, task=task_json(find_task(task_id, db, user_id)))

    @app.get('/tasks')
    def list_tasks(user_id=Depends(protected), db=Depends(get_db)):
        query = select(tasks).where(tasks.c.user_id == user_id).order_by(tasks.c.id)
        rows = db.execute(query).mappings().all()
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
        query = update(tasks).where(
            tasks.c.id == task_id,
            tasks.c.user_id == user_id,
        ).values(titulo=data['titulo'], descripcion=data['descripcion'])
        db.execute(query)
        db.commit()
        return dict(message=AUTHORIZED, task=task_json(find_task(task_id, db, user_id)))

    def set_completed(task_id, completed, db, user_id):
        if find_task(task_id, db, user_id) is None:
            raise HTTPException(status_code=404, detail='Tarea no encontrada')
        query = update(tasks).where(
            tasks.c.id == task_id,
            tasks.c.user_id == user_id,
        ).values(realizada=bool(completed))
        db.execute(query)
        db.commit()
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
