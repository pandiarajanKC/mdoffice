# MD Office — Executive Follow-up & Action Management

Enterprise web application for the Managing Director's office: meetings, decisions,
action items, projects, follow-ups, AI-assisted summarization, and executive dashboards.

This repository currently implements **Phase 1 — Foundation**: application structure,
secure authentication for all three roles (MD / EA / Employee), role-based access
control, the enterprise UI shell, and database connectivity. Later phases (Meetings,
Projects, AI Tools, Calendar, Reports, ...) build on top of this without restructuring it.

## Architecture
```
Route  →  Service  →  Repository / ORM  →  Database
```

- **Flask** application factory (`app/create_app`), organized as blueprints per module.
- **SQLAlchemy + Flask-Migrate (Alembic)** for the application's own tables.
- **Read-only repository** (`app/repositories/employee_repository.py`) over the existing
  HR master `CUSTOMER_MASTER.dbo.EmployeeList` — never duplicated, never migrated.
- **Flask-Login** for session auth, **Flask-WTF** for CSRF-protected forms.

### Two databases, one SQL Server instance

| Purpose | Database | Access |
|---|---|---|
| This application's own tables | `mdoffice` | Read/write, owned by this app |
| HR Employee Master | `CUSTOMER_MASTER.EmployeeList` | Read-only, owned by another system |

**Important:** the `mdoffice` database already contained a predecessor application's
tables (`meetings`, `action_items`, `people`, `departments`, `meeting_decisions`,
`meeting_types`, `config`, plus empty `projects`/`tasks`/`sprints`/`users`/`change_requests`)
with live data (260 people, 29 action items, 18 meetings, 30 departments, etc.) when this
project started. **Those tables are left completely untouched.** All new tables created by
this application are prefixed with `md_` (e.g. `md_app_user`, `md_activity_log`) so they can
never collide with the legacy schema. Migrating the legacy data into the new normalized
schema is a deliberate, separate step planned for Phase 3/4 once the Meeting/Task/Project
models exist — see `migrations/env.py`'s `include_object` filter, which keeps Alembic
autogenerate scoped to `md_`-prefixed tables only, so it will never propose dropping the
legacy ones.

### EmployeeList schema (as inspected live, not assumed)

| Column | Meaning |
|---|---|
| `AssociateCode` | Unique employee code — used as the **login username** for the Employee role |
| `AssociateName` | Full name |
| `Department`, `Designation`, `Location` | Org fields |
| `ManagerId` | References another employee's `AssociateCode` |
| `OffMailId` / `PerMailId` | Official / personal email (official is blank for ~57% of employees — fall back to personal) |
| `PayGroupName` | Staff / NAPS / NATS |
| `Status` | **Not usable** — every row has an empty string; account activation is tracked in this app's own `md_app_user.is_active` instead |

See `app/repositories/employee_repository.py` for the full mapping.

## Roles & Authentication

- **MD** and **EA**: seeded local accounts (`admin`, `admin1`), provisioned via
  `scripts/seed_users.py`. Passwords are never hardcoded — the script generates secure
  random passwords (or reads `SEED_MD_PASSWORD`/`SEED_EA_PASSWORD` from `.env` if you set
  them) and forces a password change on first login.
- **Employee**: logs in with their **Employee Code** (`EmployeeList.AssociateCode`) and the
  temporary password `Pass`. On first successful login the app automatically provisions an
  `md_app_user` row (hashing the password immediately) and forces an immediate password
  change before any other action is allowed. Subsequent logins use the chosen password.
- Every route is protected server-side via `@roles_required(...)` in
  `app/utils/rbac.py` — frontend visibility is never the only guard.
- Failed logins are rate-limited (`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES` in `.env`);
  all auth events are written to `logs/security.log` and the `md_activity_log` table.

## Project layout

```
app/
  config.py            Environment-driven configuration (no hardcoded credentials)
  extensions.py         Flask extension singletons (db, migrate, login, csrf)
  models/               AppUser, ActivityLog (+ shared TimestampMixin)
  repositories/         Read-only EmployeeList repository
  services/             auth_service.py — login/provisioning/password-change logic
  auth/                 Login, logout, change-password blueprint
  dashboard/             Role-specific dashboard blueprint (MD / EA / Employee)
  utils/                 RBAC decorators, logging setup
  templates/             Jinja templates (enterprise UI shell, sidebar, topbar, dashboards)
  static/                CSS/JS for the enterprise theme
migrations/              Alembic migration history
scripts/seed_users.py    Creates the initial MD/EA accounts
tests/                   pytest suite (auth service + RBAC), uses SQLite, no MSSQL needed
```

## Setup

### 1. Prerequisites

- Python 3.12+ (tested with 3.14)
- ODBC Driver 18 for SQL Server installed
- Network access to the SQL Server instance

### 2. Install dependencies

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in real values (never commit `.env`):

```env
DB_SERVER=...
DB_USERNAME=...
DB_PASSWORD=...
DB_DRIVER=ODBC Driver 18 for SQL Server
DB_APP_DATABASE=mdoffice
DB_EMPLOYEE_DATABASE=CUSTOMER_MASTER
DB_EMPLOYEE_TABLE=EmployeeList
```

### 4. Run database migrations

```powershell
$env:FLASK_APP = "run.py"
.venv\Scripts\python -m flask db upgrade
```

This creates only the `md_`-prefixed application tables. It never touches any
pre-existing tables in the target database.

### 5. Seed the initial MD/EA accounts

```powershell
.venv\Scripts\python scripts\seed_users.py
```

Save the printed temporary passwords — they are shown once and must be changed on
first login.

### 6. Run the app

```powershell
.venv\Scripts\python run.py
```

Visit http://127.0.0.1:5000 — sign in as `admin` (MD), `admin1` (EA), or any valid
Employee Code with password `Pass`.

### 7. Run tests

```powershell
.venv\Scripts\python -m pytest tests/ -v
```

Tests use an in-memory SQLite database and a fake employee repository — no live
database connection is required to run them.

## Calendar Sync setup (Google Calendar)

The Calendar page (`/calendar/`) works out of the box in a "not configured" state —
it explains what's missing rather than showing a broken button. To activate it:

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project
   (or use an existing one).
2. **APIs & Services → Library** → enable the **Google Calendar API**.
3. **APIs & Services → OAuth consent screen** → configure it (External or Internal,
   depending on your Workspace setup) with at least the `calendar.readonly` scope.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URI: the exact value you'll put in `GOOGLE_OAUTH_REDIRECT_URI`
     below (e.g. `http://127.0.0.1:5000/calendar/oauth/callback` for local dev, or your
     real domain's equivalent in UAT/production — must use HTTPS in production).
5. Copy the generated **Client ID** and **Client Secret** into `.env`:
   ```env
   GOOGLE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=...
   GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:5000/calendar/oauth/callback
   ```
6. Generate a token-encryption key (OAuth tokens are never stored in plaintext) and set it too:
   ```powershell
   .venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   ```env
   TOKEN_ENCRYPTION_KEY=<paste the generated key>
   ```
7. Restart the app. The Calendar page now shows a **Sign in with Google** button per
   MD account — clicking it sends the MD (or whoever manages the calendar) to Google's
   own sign-in and consent screen. This application never sees or stores the Google
   password, only an encrypted OAuth token pair, and only requests read-only calendar
   access.

Architecture: `app/services/calendar/base_calendar_service.py` defines a
provider-agnostic interface; `google_calendar_service.py` is the only implementation
today. Adding Microsoft Graph later means writing one new adapter class — routes,
sync logic, and templates don't change. Synced events are matched to `Meeting` rows by
`(external_calendar_event_id, calendar_provider)` to avoid duplicates on repeated
syncs, and a sync never overwrites status/confidentiality/notes that EA has already
set in-app — only calendar-owned fields (title, time, location, attendees) refresh.

## AI Tools setup (OpenAI)

The Meeting workspace's Transcript & AI tab and the standalone `/ai-tools/` Smart Action
Extractor both work out of the box in a "not configured" state — an honest notice
instead of a broken button. To activate them:

1. Get an API key from [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Set it in `.env`:
   ```env
   AI_PROVIDER=openai
   OPENAI_API_KEY=sk-...
   OPENAI_CHAT_MODEL=gpt-4o-mini
   OPENAI_TRANSCRIBE_MODEL=whisper-1
   ```
   The model names are configurable — check OpenAI's current model list if the defaults
   above are ever deprecated.
3. Restart the app. Every AI-powered button becomes active immediately.

**What each feature does, and the safety rules that are always enforced (spec sections
26-31, 56):**

- **Upload Meeting Audio** (`.mp3`/`.wav`/`.m4a`/`.webm`/`.ogg`/`.oga`/`.opus`/`.flac`/
  `.mp4`/`.mpeg`/`.mpga`/`.aac`/`.3gp`/`.amr` — broad on purpose since most recordings
  arrive as WhatsApp voice notes, size-limited) on the meeting workspace, validated by
  extension, MIME type, and size before it ever touches disk; stored under a random
  server-generated filename (`app/services/attachment_service.py`) — the original
  filename is kept only as display metadata, never trusted for a path.
- **Generate Transcript** calls OpenAI's Whisper API on the uploaded audio and saves a
  fully editable draft.
- **Generate Summary** produces a structured summary (Key Discussion Points, Decisions,
  Risks/Concerns, Action Items, Follow-up Required) from the transcript, also editable.
- **Extract Action Items** proposes candidate tasks from the summary/transcript — **never
  saved automatically**. The EA always sees an explicit review screen (edit title/
  description/priority, pick assignees, set a due date, and select which suggestions to
  keep) before anything is written as a real task. Every AI-generated block is labeled
  "AI Generated — Review Before Saving/Using" until a human has edited or approved it.
- **Smart Action Extractor** (`/ai-tools/`) is the same summarize/extract-actions
  capability for text pasted from email/WhatsApp/anywhere else, with no meeting
  attached — approved actions become `GENERAL_FOLLOWUP` tasks on the unified Task model.
- Every AI call (success or failure, with provider/model/timing) is recorded to
  `AIProcessingLog`, regardless of whether the human review step ultimately keeps
  any of the suggested output.

Architecture: `app/services/ai/base_ai_service.py` and `base_transcription_service.py`
define provider-agnostic interfaces; `openai_service.py` and
`openai_transcription_service.py` are the only implementations today. Swapping providers
(e.g. a local Whisper/Ollama setup) later means writing new adapter classes and updating
`app/services/ai/factory.py` — the business logic in `meeting_ai_service.py` and
`text_extraction_service.py`, the routes, and the human-review templates don't change.

## Deployment notes (later phase)

- Use **Waitress** instead of the Flask dev server in UAT/production
  (`APP_ENV=uat` / `APP_ENV=production` switches cookie security flags).
- Never commit `.env`; only `.env.example` is tracked.

## Roadmap

See `app/dashboard/routes.py` (`ROADMAP`) for the phase-by-phase module rollout shown
to users on their dashboard, and the original master specification for full phase details
(Meetings → Projects → Employee Portal → Dashboards → Calendar → AI Tools →
Notifications → Reports & Hardening).
#   m d o f f i c e 
 
 