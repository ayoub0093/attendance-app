# Workday attendance

A small Flask + SQLite attendance application for Windows. Employees use an ordinary phone camera to open the workplace URL, sign in, and clock in or out. No phone application is required.

## Software and project files

Python 3.13, pip, Git and VS Code were installed or checked during setup. No Node.js or Docker is used.

- `app.py`: routes, SQLite schema, authentication, attendance and administrator commands.
- `templates/`: Jinja2 employee pages, administrator dashboard and QR poster.
- `static/`: responsive CSS and small vanilla JavaScript enhancements.
- `run.py`: local development entry point.
- `test_app.py`: automated functional and security tests.
- `instance/attendance.db`: private SQLite database (created by init-db, excluded from Git).
- `.env`: private local settings and generated secret (excluded from Git).
- `requirements.txt`: dependencies; `requirements-lock.txt`: versions installed during setup.

## Installation and configuration

From PowerShell in this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

No activation is necessary. This installation already has a `.venv` and a `.env` with a randomly generated secret. For another installation copy `.env.example` to `.env`, then generate a secret with:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Put the result in SECRET_KEY in `.env`. Keep it private. Settings are loaded from environment variables first, then `.env`:

- SECRET_KEY: required random signing key; changing it logs everyone out.
- LOCAL_TIMEZONE: Europe/Paris initially. Storage and CSV timestamps use UTC.
- PUBLIC_URL: website URL encoded in the QR. Initially http://127.0.0.1:5000.
- COOKIE_SECURE: false for local HTTP only; true for HTTPS deployment.

## Initialize and create the first administrator

```powershell
.\.venv\Scripts\python.exe -m flask --app run init-db
.\.venv\Scripts\python.exe -m flask --app run create-admin
```

The second command prompts for a username, full name, and password twice. Password input is hidden. Use at least 12 characters. There are no default accounts. Repeat the command to create another administrator. Do not pass production passwords on the command line, where they can enter shell history.

## Start locally

```powershell
.\.venv\Scripts\python.exe run.py
```

Open http://127.0.0.1:5000 and sign in with the administrator you created. The server binds only to this PC and debug mode is off. Stop with Ctrl+C. Log out using the header button.

## Employees and attendance

In Administration, select Add employee. Give the employee a unique username, full name and password, and share their credentials privately. Edit / reset password changes information, replaces a password, or deactivates an employee. Deactivation immediately denies subsequent requests and closes an open session at deactivation time. Administrators cannot be deactivated from this interface. This MVP does not offer employee self-service password changes.

Employees see only their own records and can clock in or out multiple times each day. The server records UTC timestamps. An SQLite transaction and unique partial index prevent concurrent duplicate clock-ins. Clock-out requires an open session. A previous day's open session remains available to close.

The dashboard lists all accounts and everyone with an open session, plus attendance filtered by local date and employee. Totals include open sessions through the time the page is loaded. Refresh for a current total. Overnight sessions are split at local midnight, including daylight saving changes. CSV exports apply the same filters and include UTC timestamps plus hours within the chosen local date; spreadsheet formula prefixes in names are escaped.

## QR code and phones

In Administration, choose View printable poster or Download PNG. The QR contains only PUBLIC_URL, never credentials. Configure the permanent URL before printing. Changing the public URL requires printing a new QR.

**Localhost and 127.0.0.1 refer to the device opening the URL. They cannot let a phone reach this PC over the internet.** The installed server is deliberately local only. For a temporary trusted Wi-Fi test, after deciding to allow LAN access, use:

```powershell
.\.venv\Scripts\python.exe -m flask --app run run --host 0.0.0.0 --port 5000
```

Set PUBLIC_URL to this PC's LAN IPv4 address (find it with `ipconfig`), e.g. http://192.168.1.20:5000, restart, allow inbound traffic only on a trusted private network, and connect the phone to that same network. HTTP does not encrypt passwords; use disposable test credentials for this temporary test. No firewall changes or network exposure were made by setup. Use HTTPS for actual employee use.

A static QR can be photographed and shared: it is a link, not proof of presence. Optional future controls include workplace network restrictions or rotating codes. These are outside this MVP.

## Production with HTTPS

Publishing requires a separate explicit decision. Do not use Flask's development server for production. On a controlled host install the requirements, create a strong private SECRET_KEY, set PUBLIC_URL=https://your-domain.example and COOKIE_SECURE=true, initialize the database, and create an administrator interactively. Run Waitress on loopback:

```powershell
.\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8000 run:app
```

Put a maintained reverse proxy such as IIS or Caddy in front of Waitress, terminate HTTPS with a valid certificate, and forward requests to 127.0.0.1:8000. Keep the application port private, restrict filesystem access to the database and `.env`, use a dedicated service account, configure service restart and backups, and keep dependencies updated. Secure cookies require HTTPS and prevent login on plain HTTP. Debug remains off. The app intentionally does not trust forwarded client-IP headers; when behind a proxy the shared IP rate limit may cover the whole company. Configure trusted proxy handling for your specific topology before larger deployments.

Security includes Werkzeug password hashing, HTTP-only SameSite sessions, CSRF forms, role checks on every admin route, parameterized SQL, validation, database-backed account/IP login limits (10 account failures or 40 IP failures per 15 minutes), and response security headers. Successful login rotates the session token. Password resets do not terminate existing sessions; deactivate an account to revoke access. Session validity is 12 hours.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use temporary databases and never write test accounts into the real database.

## Backups and restore

Use SQLite's backup API to make a consistent copy, even while the app is running:

```powershell
.\.venv\Scripts\python.exe backup.py C:\Backups\attendance-backup.db
```

Choose an existing restricted backup directory and a new filename. Keep backups private and encrypted; they contain employee records and password hashes. Protect `.env` separately. To restore, stop the app, preserve the current database, replace `instance/attendance.db` with the backup, and restart. Test restores periodically. No retention policy or external backup service is configured.

The employee Current session card counts from the latest Clock In (including across midnight), resets to 0h 00m after Clock Out, and excludes completed sessions. Attendance history and administrator daily totals remain saved.
