import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import click
import qrcode
from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

UTC = timezone.utc

def now():
    return datetime.now(UTC)

def stamp(value=None):
    return (value or now()).isoformat(timespec='microseconds')

def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def create_app(config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY'), DATABASE=str(Path(app.instance_path) / 'attendance.db'), LOCAL_TIMEZONE=os.getenv('LOCAL_TIMEZONE', 'Europe/Paris'), PUBLIC_URL=os.getenv('PUBLIC_URL', 'http://127.0.0.1:5000'), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', 'false').lower() == 'true', PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=65536)
    if config:
        app.config.update(config)
    if not app.config['SECRET_KEY']:
        raise RuntimeError('Set SECRET_KEY in .env. See README.md.')
    local_zone = ZoneInfo(app.config['LOCAL_TIMEZONE'])
    Path(app.instance_path).mkdir(exist_ok=True)

    def db():
        if 'db' not in g:
            g.db = connect(app.config['DATABASE'])
        return g.db

    @app.teardown_appcontext
    def close(error):
        connection = g.pop('db', None)
        if connection:
            connection.close()

    def init_db():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS employees (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
          password_hash TEXT NOT NULL, full_name TEXT NOT NULL,
          is_admin INTEGER NOT NULL DEFAULT 0 CHECK(is_admin IN (0,1)),
          is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attendance (
          id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
          clock_in TEXT NOT NULL, clock_out TEXT, CHECK(clock_out IS NULL OR clock_out >= clock_in));
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_session ON attendance(employee_id) WHERE clock_out IS NULL;
        CREATE INDEX IF NOT EXISTS attendance_date ON attendance(clock_in);
        CREATE INDEX IF NOT EXISTS attendance_employee ON attendance(employee_id, clock_in);
        CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, since TEXT NOT NULL);
        ''')

    @app.cli.command('init-db')
    def initialize():
        init_db()
        click.echo('Database initialized.')

    @app.cli.command('create-admin')
    @click.option('--username', prompt=True)
    @click.option('--name', prompt='Full name')
    @click.password_option(confirmation_prompt=True)
    def create_admin(username, name, password):
        init_db()
        if not valid_user(username, name) or len(password) < 12:
            raise click.ClickException('Use a 3–40 character username, a name, and a password of at least 12 characters.')
        try:
            db().execute('INSERT INTO employees(username,password_hash,full_name,is_admin,created_at) VALUES(?,?,?,1,?)', (username.strip(), generate_password_hash(password), name.strip(), stamp()))
            db().commit()
        except sqlite3.IntegrityError:
            raise click.ClickException('Username already exists.')
        click.echo('Administrator created.')

    def valid_user(username, name):
        return re.fullmatch(r'[A-Za-z0-9_.-]{3,40}', username.strip()) and 1 <= len(name.strip()) <= 100

    @app.template_filter('localtime')
    def localtime(value):
        return datetime.fromisoformat(value).astimezone(local_zone).strftime('%d %b %Y, %H:%M:%S') if value else 'Working'

    def duration(row, start=None, end=None):
        arrival = datetime.fromisoformat(row['clock_in'])
        departure = datetime.fromisoformat(row['clock_out']) if row['clock_out'] else now()
        if start:
            arrival = max(arrival, start)
        if end:
            departure = min(departure, end)
        return max(0, (departure - arrival).total_seconds()) / 3600

    @app.template_filter('worktime')
    def worktime(value):
        total_minutes = max(0, int(float(value) * 60 + 1e-7))
        hours, minutes = divmod(total_minutes, 60)
        return f'{hours}h {minutes:02d}m'

    app.jinja_env.globals['hours'] = duration

    @app.before_request
    def security():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        g.user = None
        if session.get('user_id'):
            g.user = db().execute('SELECT * FROM employees WHERE id=? AND is_active=1', (session['user_id'],)).fetchone()
            if not g.user:
                session.clear()
                return redirect(url_for('login'))
        if request.method == 'POST':
            if not hmac.compare_digest(str(session.get('csrf', '')), request.form.get('csrf', '')):
                abort(400, 'Invalid form token. Reload the page and try again.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        if app.config['SESSION_COOKIE_SECURE']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    def protected(admin=False):
        def decorate(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                if not g.user:
                    return redirect(url_for('login'))
                if admin and not g.user['is_admin']:
                    abort(403)
                return fn(*args, **kwargs)
            return wrapper
        return decorate

    dummy_hash = generate_password_hash(secrets.token_urlsafe(24))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()[:100]
            # Separate account and IP buckets also limit password spraying.
            keys = ['user:' + username.casefold(), 'ip:' + (request.remote_addr or 'unknown')]
            cutoff = stamp(now() - timedelta(minutes=15))
            connection = db()
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('DELETE FROM login_attempts WHERE since < ?', (cutoff,))
            attempts = [connection.execute('SELECT failures FROM login_attempts WHERE key=?', (key,)).fetchone() for key in keys]
            if any(row and row['failures'] >= limit for row, limit in zip(attempts, [10, 40])):
                connection.commit()
                return render_template('login.html', error='Too many attempts. Try again in 15 minutes.'), 429
            user = connection.execute('SELECT * FROM employees WHERE username=?', (username,)).fetchone()
            valid = check_password_hash(user['password_hash'] if user else dummy_hash, request.form.get('password', ''))
            if not user or not valid or not user['is_active']:
                for key in keys:
                    connection.execute('INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=failures+1', (key, stamp()))
                connection.commit()
                return render_template('login.html', error='Invalid username or password.'), 401
            connection.execute('DELETE FROM login_attempts WHERE key=?', (keys[0],))
            connection.commit()
            session.clear()
            session.update(user_id=user['id'], csrf=secrets.token_urlsafe(32))
            session.permanent = True
            return redirect(url_for('admin' if user['is_admin'] else 'home'))
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    def day_bounds(date_text):
        try:
            day = datetime.strptime(date_text, '%Y-%m-%d').date()
        except ValueError:
            abort(400, 'Invalid date.')
        start = datetime.combine(day, datetime.min.time(), local_zone)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), local_zone)
        return start.astimezone(UTC), end.astimezone(UTC)

    @app.get('/')
    @protected()
    def home():
        start, end = day_bounds(now().astimezone(local_zone).date().isoformat())
        rows = db().execute('SELECT * FROM attendance WHERE employee_id=? AND clock_in < ? AND (clock_out IS NULL OR clock_out > ?) ORDER BY clock_in DESC', (g.user['id'], stamp(end), stamp(start))).fetchall()
        active = db().execute('SELECT * FROM attendance WHERE employee_id=? AND clock_out IS NULL', (g.user['id'],)).fetchone()
        return render_template('home.html', rows=rows, active=active, total=duration(active) if active else 0)

    @app.post('/clock/<action>')
    @protected()
    def clock(action):
        if action not in ('in', 'out'):
            abort(404)
        connection = db()
        connection.execute('BEGIN IMMEDIATE')
        active = connection.execute('SELECT id FROM attendance WHERE employee_id=? AND clock_out IS NULL', (g.user['id'],)).fetchone()
        recorded = stamp()
        if action == 'in' and not active:
            connection.execute('INSERT INTO attendance(employee_id,clock_in) VALUES(?,?)', (g.user['id'], recorded))
        elif action == 'out' and active:
            connection.execute('UPDATE attendance SET clock_out=? WHERE id=?', (recorded, active['id']))
        else:
            connection.rollback()
            flash('You are already clocked in.' if active else 'Clock in before clocking out.')
            return redirect(url_for('home'))
        connection.commit()
        flash('Clocked ' + action + ' at ' + localtime(recorded))
        return redirect(url_for('home'))

    def filtered_records():
        date = request.args.get('date', now().astimezone(local_zone).date().isoformat())
        start, end = day_bounds(date)
        employee = request.args.get('employee', '')
        sql = 'SELECT a.*,e.full_name,e.username FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE a.clock_in < ? AND (a.clock_out IS NULL OR a.clock_out > ?)'
        params = [stamp(end), stamp(start)]
        if employee:
            if not employee.isdigit():
                abort(400, 'Invalid employee.')
            sql += ' AND employee_id=?'
            params.append(int(employee))
        rows = db().execute(sql + ' ORDER BY a.clock_in DESC', params).fetchall()
        return rows, date, employee, start, end

    @app.get('/admin')
    @protected(admin=True)
    def admin():
        rows, date, employee, start, end = filtered_records()
        people = db().execute('SELECT * FROM employees ORDER BY full_name').fetchall()
        working = db().execute('SELECT e.full_name FROM employees e JOIN attendance a ON e.id=a.employee_id WHERE a.clock_out IS NULL').fetchall()
        return render_template('admin.html', rows=rows, people=people, working=working, date=date, employee=employee, total=sum(duration(row, start, end) for row in rows), start=start, end=end)

    @app.route('/admin/employees/new', methods=['GET', 'POST'])
    @app.route('/admin/employees/<int:employee_id>', methods=['GET', 'POST'])
    @protected(admin=True)
    def edit_employee(employee_id=None):
        person = db().execute('SELECT * FROM employees WHERE id=?', (employee_id,)).fetchone() if employee_id else None
        if employee_id and not person:
            abort(404)
        if request.method == 'POST':
            username, name = request.form.get('username', '').strip(), request.form.get('full_name', '').strip()
            password = request.form.get('password', '')
            active = int(request.form.get('is_active') == '1')
            error = None
            if not valid_user(username, name):
                error = 'Use a 3–40 character username (letters, digits, _, ., -) and a name up to 100 characters.'
            elif (not person or password) and len(password) < 12:
                error = 'Passwords must have at least 12 characters.'
            elif person and person['is_admin'] and not active:
                error = 'Administrator accounts cannot be deactivated here.'
            if error:
                flash(error)
                return render_template('employee.html', person=person), 400
            try:
                if person:
                    db().execute('UPDATE employees SET username=?,full_name=?,is_active=? WHERE id=?', (username, name, active, employee_id))
                    if password:
                        db().execute('UPDATE employees SET password_hash=? WHERE id=?', (generate_password_hash(password), employee_id))
                    if not active:
                        db().execute('UPDATE attendance SET clock_out=? WHERE employee_id=? AND clock_out IS NULL', (stamp(), employee_id))
                else:
                    db().execute('INSERT INTO employees(username,password_hash,full_name,is_active,created_at) VALUES(?,?,?,?,?)', (username, generate_password_hash(password), name, active, stamp()))
                db().commit()
            except sqlite3.IntegrityError:
                db().rollback()
                flash('That username is already used.')
                return render_template('employee.html', person=person), 400
            flash('Employee saved.' + (' Open attendance was closed on deactivation.' if not active else ''))
            return redirect(url_for('admin'))
        return render_template('employee.html', person=person)

    @app.get('/admin/export.csv')
    @protected(admin=True)
    def export():
        rows, date, employee, start, end = filtered_records()
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)
        writer.writerow(['Employee', 'Username', 'Clock in (UTC)', 'Clock out (UTC)', 'Hours on selected local date', 'Local date'])
        def safe(value):
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
        for row in rows:
            writer.writerow([safe(row['full_name']), safe(row['username']), row['clock_in'], row['clock_out'] or '', f'{duration(row, start, end):.4f}', date])
        return send_file(io.BytesIO(buffer.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name='attendance-' + date + '.csv')

    @app.get('/admin/qr.png')
    @protected(admin=True)
    def qr():
        public = app.config['PUBLIC_URL'].rstrip('/') + '/'
        if urlparse(public).scheme not in ('http', 'https') or not urlparse(public).netloc:
            abort(400, 'PUBLIC_URL must be an absolute http or https URL.')
        buffer = io.BytesIO()
        qrcode.make(public).save(buffer, format='PNG')
        buffer.seek(0)
        return send_file(buffer, mimetype='image/png', as_attachment=request.args.get('download') == '1', download_name='workplace-qr.png')

    @app.get('/admin/qr')
    @protected(admin=True)
    def qr_print():
        return render_template('qr.html', public_url=app.config['PUBLIC_URL'])

    return app
