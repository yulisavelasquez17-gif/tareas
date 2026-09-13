from contextlib import closing
import hashlib
import sqlite3
import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import create_app, AUTHORIZED, UNAUTHORIZED


class APITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.client = TestClient(create_app(self.path))
        self.credentials = dict(nombre='Ana', apellido='Pérez', usuario='ana', contrasena='clave-segura-123')
        self.assertEqual(self.client.post('/register', json=self.credentials).status_code, 201)
        self.login_credentials = {key: self.credentials[key] for key in ('usuario', 'contrasena')}
        response = self.client.post('/login', json=self.login_credentials)
        self.token = response.json()['token']
        self.headers = {'Authorization': 'Bearer ' + self.token}

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_task_lifecycle(self):
        response = self.client.post('/tasks', headers=self.headers, json={'titulo': 'Estudiar'})
        self.assertEqual(response.status_code, 201)
        task_id = response.json()['task']['id']
        url = f'/tasks/{task_id}'
        self.assertEqual(self.client.get('/tasks', headers=self.headers).json()['tasks'][0]['id'], task_id)
        self.assertEqual(self.client.get(url, headers=self.headers).json()['message'], AUTHORIZED)
        response = self.client.put(url, headers=self.headers, json={'titulo': 'Leer', 'descripcion': ''})
        self.assertEqual(response.json()['task']['titulo'], 'Leer')
        for suffix, value in [('complete', True), ('complete', True), ('uncomplete', False)]:
            self.assertEqual(self.client.put(url + '/' + suffix, headers=self.headers).json()['task']['realizada'], value)

    def test_all_task_routes_require_token(self):
        for method, url in [('post', '/tasks'), ('get', '/tasks'), ('get', '/tasks/1'), ('put', '/tasks/1'), ('put', '/tasks/1/complete'), ('put', '/tasks/1/uncomplete')]:
            for header in ['', 'Bearer invalid', 'Basic invalid']:
                response = getattr(self.client, method)(url, headers={'Authorization': header})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['message'], UNAUTHORIZED)

    def test_user_isolation(self):
        task = self.client.post('/tasks', headers=self.headers, json={'titulo': 'Privada'}).json()['task']
        other = dict(self.credentials, usuario='otro')
        self.client.post('/register', json=other)
        headers = {'Authorization': 'Bearer ' + self.client.post('/login', json={key: other[key] for key in ('usuario', 'contrasena')}).json()['token']}
        self.assertEqual(self.client.get('/tasks', headers=headers).json()['tasks'], [])
        for method, suffix in [('get', ''), ('put', ''), ('put', '/complete'), ('put', '/uncomplete')]:
            response = self.client.request(method, f"/tasks/{task['id']}" + suffix, headers=headers, json={'titulo': 'Otro', 'descripcion': ''})
            self.assertEqual(response.status_code, 404)

    def test_credentials_and_validation(self):
        self.assertEqual(self.client.post('/register', json=self.credentials).status_code, 409)
        for key in self.login_credentials:
            response = self.client.post('/login', json=dict(self.login_credentials, **{key: 'incorrecto'}))
            self.assertEqual(response.status_code, 401)
        for payload in [[], {}, {'titulo': ' '}, {'titulo': 3}, {'titulo': 'A', 'user_id': 2}]:
            self.assertEqual(self.client.post('/tasks', headers=self.headers, json=payload).status_code, 400)
        self.assertEqual(self.client.post('/tasks', content='{', headers={**self.headers, 'Content-Type': 'application/json'}).status_code, 400)

    def test_fastapi_schema_and_token_duration(self):
        schema = self.client.get('/openapi.json').json()
        self.assertEqual(set(schema['components']['schemas']['LoginBody']['required']), {'usuario', 'contrasena'})
        self.assertEqual(self.client.get('/docs').status_code, 200)
        with patch('app.time.time', return_value=2000000000):
            response = self.client.post('/login', json=self.login_credentials)
        self.assertEqual(response.json()['expires_at'], 2000003600)

    def test_storage_expiration_and_foreign_keys(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertNotEqual(db.execute('SELECT password_hash FROM users').fetchone()[0], self.credentials['contrasena'])
            self.assertEqual(db.execute('SELECT token_hash FROM tokens').fetchone()[0], hashlib.sha256(self.token.encode()).hexdigest())
            db.execute('PRAGMA foreign_keys = ON')
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO tokens (user_id, token_hash, expires_at) VALUES (999, 'fake', 999)")
            db.execute('UPDATE tokens SET expires_at = 0')
        self.assertEqual(self.client.get('/tasks', headers=self.headers).status_code, 401)
        client = TestClient(create_app(self.path))
        self.assertEqual(client.post('/login', json=self.login_credentials).status_code, 200)


if __name__ == '__main__':
    unittest.main()
