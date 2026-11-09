import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash
from app import create_app, connect, stamp

@pytest.fixture
def app(tmp_path):
    application = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': str(tmp_path / 'test.db')})
    with application.app_context():
        result = application.test_cli_runner().invoke(args=['init-db'])
        assert result.exit_code == 0
        db = connect(application.config['DATABASE'])
        for username, admin in [('worker',0),('manager',1),('other',0)]:
            db.execute('INSERT INTO employees(username,password_hash,full_name,is_admin,created_at) VALUES(?,?,?,?,?)', (username,generate_password_hash('a-secure-password'),username,admin,stamp()))
        db.commit()
        db.close()
    return application

@pytest.fixture
def client(app):
    return app.test_client()

def token(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf']

def post(client, path, data=None):
    return client.post(path, data={'csrf': token(client), **(data or {})})

def login(client, username='worker', password='a-secure-password'):
    return post(client,'/login',{'username':username,'password':password})

def rows(app):
    with connect(app.config['DATABASE']) as db:
        return db.execute('SELECT * FROM attendance').fetchall()

def test_login(client):
    assert login(client).status_code == 302
    assert b'Hello, worker' in client.get('/').data

def test_invalid_login(client):
    assert login(client,password='wrong').status_code == 401
    assert client.get('/').status_code == 302

def test_access_restrictions(client):
    for path in ['/','/admin','/admin/export.csv','/admin/qr.png']:
        assert client.get(path).status_code == 302
    login(client)
    for path in ['/admin','/admin/export.csv','/admin/qr.png','/admin/qr','/admin/employees/new','/admin/employees/2']:
        assert client.get(path).status_code == 403
    assert post(client,'/admin/employees/new').status_code == 403

def test_admin(client):
    login(client,'manager')
    assert client.get('/admin').status_code == 200
    assert client.get('/admin/qr.png').data.startswith(b'\x89PNG')
    assert client.get('/admin/qr').status_code == 200

def test_clock_in_out(app,client):
    login(client)
    post(client,'/clock/in')
    assert len(rows(app)) == 1
    assert rows(app)[0]['clock_out'] is None
    post(client,'/clock/out')
    assert rows(app)[0]['clock_out'] is not None

def test_duplicate_clock_in(app,client):
    login(client)
    post(client,'/clock/in')
    post(client,'/clock/in')
    assert len(rows(app)) == 1

def test_clock_out_without_clock_in(app,client):
    login(client)
    post(client,'/clock/out')
    assert not rows(app)

def test_multiple_sessions(app,client):
    login(client)
    for _ in range(2):
        post(client,'/clock/in')
        post(client,'/clock/out')
    assert len(rows(app)) == 2

def test_own_records_only(app,client):
    with connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO attendance(employee_id,clock_in) VALUES(3,?)',(stamp(),))
        db.commit()
    login(client)
    assert b'No attendance recorded today' in client.get('/').data
    post(client,'/clock/out')
    assert rows(app)[0]['clock_out'] is None

def test_hours_and_csv(app,client):
    with connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO attendance(employee_id,clock_in,clock_out) VALUES(1,?,?)',('2026-11-08T22:00:00.000000+00:00','2026-11-09T02:00:00.000000+00:00'))
        db.commit()
    login(client,'manager')
    page = client.get('/admin?date=2026-11-09').data
    assert b'3h 00m' in page
    response = client.get('/admin/export.csv?date=2026-11-09')
    assert response.status_code == 200
    assert b'3.0000,2026-11-09' in response.data
    assert 'attachment' in response.headers['Content-Disposition']
    assert b'worker' not in client.get('/admin/export.csv?date=2026-11-09&employee=3').data

def test_csrf(client):
    login(client)
    assert client.post('/clock/in').status_code == 400
    assert client.get('/logout').status_code == 405

def test_rate_limit(client):
    for _ in range(10):
        assert login(client,password='bad').status_code == 401
    assert login(client).status_code == 429

def test_employee_management(app,client):
    login(client,'manager')
    data={'username':'newperson','full_name':'New Person','password':'initial-password','is_active':'1'}
    assert post(client,'/admin/employees/new',data).status_code == 302
    assert post(client,'/admin/employees/new',data).status_code == 400
    assert post(client,'/admin/employees/4',{'username':'renamed','full_name':'New Name','password':'replacement-password','is_active':'1'}).status_code == 302
    post(client,'/logout')
    assert login(client,'renamed','initial-password').status_code == 401
    assert login(client,'renamed','replacement-password').status_code == 302
    post(client,'/clock/in')
    post(client,'/logout')
    login(client,'manager')
    assert post(client,'/admin/employees/4',{'username':'renamed','full_name':'New Name'}).status_code == 302
    assert rows(app)[0]['clock_out'] is not None
    post(client,'/logout')
    assert login(client,'renamed','replacement-password').status_code == 401

def test_session_revoked_on_deactivation(app,client):
    login(client)
    with connect(app.config['DATABASE']) as db:
        db.execute('UPDATE employees SET is_active=0 WHERE id=1')
        db.commit()
    assert client.get('/').status_code == 302

def test_concurrent_clock_in(app):
    def clock_in():
        c=app.test_client()
        login(c)
        return post(c,'/clock/in').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _:clock_in(),range(2))) == [302,302]
    assert len(rows(app)) == 1

def test_create_admin_command(app):
    result = app.test_cli_runner().invoke(args=['create-admin','--username','boss','--name','Boss','--password','long-safe-password'])
    assert result.exit_code == 0, result.output
    result = app.test_cli_runner().invoke(args=['create-admin','--username','boss','--name','Boss','--password','long-safe-password'])
    assert result.exit_code != 0

def test_validation(client):
    login(client,'manager')
    assert client.get('/admin?date=invalid').status_code == 400
    assert client.get('/admin?employee=nope').status_code == 400
    assert post(client,'/admin/employees/new',{'username':'x','full_name':'','password':'bad'}).status_code == 400
    assert post(client,'/admin/employees/2',{'username':'manager','full_name':'Manager'}).status_code == 400

def test_current_session_resets_after_checkout(app, client):
    from datetime import timedelta
    from app import now
    login(client)
    with connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO attendance(employee_id,clock_in,clock_out) VALUES(1,?,?)', (stamp(now()-timedelta(hours=5)), stamp(now()-timedelta(hours=3))))
        db.execute('INSERT INTO attendance(employee_id,clock_in) VALUES(1,?)', (stamp(now()-timedelta(hours=1,minutes=30)),))
        db.commit()
    page = client.get('/').data
    assert b'CURRENT SESSION' in page
    assert b'1h 30m' in page
    post(client, '/clock/out')
    assert b'0h 00m' in client.get('/').data
    saved = rows(app)
    assert len(saved) == 2
    assert all(row['clock_out'] for row in saved)
    post(client, '/clock/in')
    assert b'0h 00m' in client.get('/').data
    assert len(rows(app)) == 3

def test_current_session_includes_time_before_midnight(app, client):
    from datetime import timedelta
    from app import now
    login(client)
    with connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO attendance(employee_id,clock_in) VALUES(1,?)', (stamp(now()-timedelta(hours=26,minutes=15)),))
        db.commit()
    assert b'26h 15m' in client.get('/').data
