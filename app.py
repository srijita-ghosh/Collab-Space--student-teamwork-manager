import os
import re
import secrets
from functools import wraps
from datetime import datetime

import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, abort
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "change-this-development-secret-key"),
    WTF_CSRF_TIME_LIMIT=None,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
)
csrf = CSRFProtect(app)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "collabspace"),
    "charset": "utf8mb4",
    "use_unicode": True,
}

ALLOWED_STATUSES = {"Pending", "In Progress", "Completed"}
ALLOWED_PRIORITIES = {"Low", "Medium", "High"}


def get_db():
    """Create a new MySQL connection for the current operation."""
    return mysql.connector.connect(**DB_CONFIG)


def fetch_one(sql, params=()):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def fetch_all(sql, params=()):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def execute(sql, params=()):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def current_user():
    if "user" not in g:
        # Set the cache first so an error page does not retry a failed DB lookup.
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            g.user = fetch_one(
                "SELECT user_id, full_name, email, created_at FROM users WHERE user_id = %s",
                (user_id,),
            )
            if not g.user:
                session.clear()
    return g.user


@app.before_request
def load_user():
    # Avoid a database lookup for static assets and public requests without a session.
    if request.endpoint != "static":
        current_user()


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "year": datetime.now().year}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in to continue.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def get_membership(team_id, user_id=None):
    user_id = user_id or current_user()["user_id"]
    return fetch_one(
        "SELECT role FROM team_members WHERE team_id = %s AND user_id = %s",
        (team_id, user_id),
    )


def leader_required(team_id):
    membership = get_membership(team_id)
    if not membership:
        abort(403)
    if membership["role"] != "leader":
        flash("Only the team leader can do that.", "warning")
        return False
    return True


def make_invite_code():
    return "COLLAB-" + secrets.token_hex(3).upper()


def normalise_text(value):
    return (value or "").strip()


def get_team_overview(user_id):
    return fetch_all(
        """SELECT t.team_id, t.team_name, t.description, t.invite_code,
                  t.created_at, tm.role,
                  (SELECT COUNT(*) FROM team_members x WHERE x.team_id = t.team_id) AS member_count,
                  (SELECT COUNT(*) FROM tasks k WHERE k.team_id = t.team_id) AS task_count
           FROM team_members tm
           JOIN teams t ON t.team_id = tm.team_id
           WHERE tm.user_id = %s
           ORDER BY t.created_at DESC""",
        (user_id,),
    )


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = normalise_text(request.form.get("full_name"))
        email = normalise_text(request.form.get("email")).lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(name) < 2:
            flash("Please enter your full name.", "danger")
        elif not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
            flash("Enter a valid email address.", "danger")
        elif len(password) < 8:
            flash("Your password must contain at least 8 characters.", "danger")
        elif password != confirm:
            flash("The passwords do not match.", "danger")
        elif fetch_one("SELECT user_id FROM users WHERE email = %s", (email,)):
            flash("An account with this email already exists. Please log in.", "warning")
        else:
            execute(
                "INSERT INTO users (full_name, email, password_hash) VALUES (%s, %s, %s)",
                (name, email, generate_password_hash(password)),
            )
            flash("Your account is ready. Log in to create your workspace.", "success")
            return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = normalise_text(request.form.get("email")).lower()
        password = request.form.get("password", "")
        user = fetch_one("SELECT * FROM users WHERE email = %s", (email,))
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password. Please try again.", "danger")
        else:
            session.clear()
            session["user_id"] = user["user_id"]
            flash(f"Welcome back, {user['full_name'].split()[0]}!", "success")
            return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    stats = fetch_one(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(status = 'Pending'), 0) AS pending,
                  COALESCE(SUM(status = 'In Progress'), 0) AS in_progress,
                  COALESCE(SUM(status = 'Completed'), 0) AS completed
           FROM tasks WHERE assigned_to = %s""",
        (user["user_id"],),
    )
    teams = get_team_overview(user["user_id"])
    recent_tasks = fetch_all(
        """SELECT k.task_id, k.title, k.priority, k.status, k.deadline,
                  t.team_name, u.full_name AS creator_name
           FROM tasks k
           JOIN teams t ON t.team_id = k.team_id
           JOIN users u ON u.user_id = k.created_by
           WHERE k.assigned_to = %s
           ORDER BY k.updated_at DESC LIMIT 6""",
        (user["user_id"],),
    )
    return render_template("dashboard.html", stats=stats, teams=teams, recent_tasks=recent_tasks)


@app.route("/my-tasks")
@login_required
def my_tasks():
    tasks = fetch_all(
        """SELECT k.*, t.team_name, u.full_name AS creator_name
           FROM tasks k
           JOIN teams t ON t.team_id = k.team_id
           JOIN users u ON u.user_id = k.created_by
           WHERE k.assigned_to = %s
           ORDER BY FIELD(k.status, 'In Progress', 'Pending', 'Completed'),
                    k.deadline IS NULL, k.deadline ASC, k.updated_at DESC""",
        (current_user()["user_id"],),
    )
    return render_template("my_tasks.html", tasks=tasks)


@app.route("/teams")
@login_required
def teams():
    return render_template("teams.html", teams=get_team_overview(current_user()["user_id"]))


@app.post("/teams/create")
@login_required
def create_team():
    team_name = normalise_text(request.form.get("team_name"))
    description = normalise_text(request.form.get("description"))
    if len(team_name) < 3:
        flash("Team name must have at least 3 characters.", "danger")
        return redirect(url_for("teams"))

    conn = get_db()
    cursor = conn.cursor()
    try:
        invite_code = make_invite_code()
        # Regenerate in the very unlikely event of a collision.
        while True:
            cursor.execute("SELECT team_id FROM teams WHERE invite_code = %s", (invite_code,))
            if not cursor.fetchone():
                break
            invite_code = make_invite_code()
        cursor.execute(
            "INSERT INTO teams (team_name, description, leader_id, invite_code) VALUES (%s, %s, %s, %s)",
            (team_name, description, current_user()["user_id"], invite_code),
        )
        team_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, 'leader')",
            (team_id, current_user()["user_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    flash(f"Team created! Share this invite code: {invite_code}", "success")
    return redirect(url_for("workspace", team_id=team_id))


@app.post("/teams/join")
@login_required
def join_team():
    code = normalise_text(request.form.get("invite_code")).upper()
    team = fetch_one("SELECT team_id, team_name FROM teams WHERE invite_code = %s", (code,))
    if not team:
        flash("That invitation code is not valid. Check it and try again.", "danger")
    elif get_membership(team["team_id"]):
        flash("You are already a member of this team.", "info")
    else:
        execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, 'member')",
            (team["team_id"], current_user()["user_id"]),
        )
        flash(f"You have joined {team['team_name']}!", "success")
        return redirect(url_for("workspace", team_id=team["team_id"]))
    return redirect(url_for("teams"))


@app.route("/teams/<int:team_id>")
@login_required
def workspace(team_id):
    membership = get_membership(team_id)
    if not membership:
        abort(403)
    team = fetch_one(
        """SELECT t.*, u.full_name AS leader_name,
                  (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id = t.team_id) AS member_count
           FROM teams t JOIN users u ON u.user_id = t.leader_id
           WHERE t.team_id = %s""",
        (team_id,),
    )
    if not team:
        abort(404)
    members = fetch_all(
        """SELECT u.user_id, u.full_name, u.email, tm.role, tm.joined_at
           FROM team_members tm JOIN users u ON u.user_id = tm.user_id
           WHERE tm.team_id = %s
           ORDER BY FIELD(tm.role, 'leader', 'member'), u.full_name""",
        (team_id,),
    )
    tasks = fetch_all(
        """SELECT k.*, assignee.full_name AS assignee_name, creator.full_name AS creator_name
           FROM tasks k
           JOIN users assignee ON assignee.user_id = k.assigned_to
           JOIN users creator ON creator.user_id = k.created_by
           WHERE k.team_id = %s
           ORDER BY FIELD(k.status, 'Pending', 'In Progress', 'Completed'),
                    k.deadline IS NULL, k.deadline ASC, k.created_at DESC""",
        (team_id,),
    )
    task_stats = {
        "total": len(tasks),
        "pending": sum(1 for task in tasks if task["status"] == "Pending"),
        "progress": sum(1 for task in tasks if task["status"] == "In Progress"),
        "completed": sum(1 for task in tasks if task["status"] == "Completed"),
    }
    return render_template(
        "workspace.html", team=team, members=members, tasks=tasks,
        membership=membership, task_stats=task_stats,
    )


@app.post("/teams/<int:team_id>/tasks/create")
@login_required
def create_task(team_id):
    if not get_membership(team_id):
        abort(403)
    if not leader_required(team_id):
        return redirect(url_for("workspace", team_id=team_id))

    title = normalise_text(request.form.get("title"))
    description = normalise_text(request.form.get("description"))
    assignee = request.form.get("assigned_to", type=int)
    priority = request.form.get("priority", "Medium")
    deadline = normalise_text(request.form.get("deadline")) or None

    if len(title) < 3:
        flash("Task title must have at least 3 characters.", "danger")
    elif priority not in ALLOWED_PRIORITIES:
        flash("Please choose a valid priority.", "danger")
    elif not assignee or not get_membership(team_id, assignee):
        flash("Choose a valid member of this team.", "danger")
    else:
        execute(
            """INSERT INTO tasks (team_id, title, description, assigned_to, created_by, priority, status, deadline)
               VALUES (%s, %s, %s, %s, %s, %s, 'Pending', %s)""",
            (team_id, title, description, assignee, current_user()["user_id"], priority, deadline),
        )
        flash("New task created and assigned successfully.", "success")
    return redirect(url_for("workspace", team_id=team_id))


@app.post("/teams/<int:team_id>/tasks/<int:task_id>/status")
@login_required
def update_task_status(team_id, task_id):
    membership = get_membership(team_id)
    if not membership:
        abort(403)
    task = fetch_one("SELECT assigned_to FROM tasks WHERE task_id = %s AND team_id = %s", (task_id, team_id))
    if not task:
        abort(404)
    if membership["role"] != "leader" and task["assigned_to"] != current_user()["user_id"]:
        abort(403)
    status = request.form.get("status")
    if status not in ALLOWED_STATUSES:
        flash("Please choose a valid task status.", "danger")
    else:
        execute(
            "UPDATE tasks SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE task_id = %s AND team_id = %s",
            (status, task_id, team_id),
        )
        flash("Task status updated.", "success")
    return redirect(url_for("workspace", team_id=team_id))


@app.post("/teams/<int:team_id>/tasks/<int:task_id>/edit")
@login_required
def edit_task(team_id, task_id):
    if not get_membership(team_id):
        abort(403)
    if not leader_required(team_id):
        return redirect(url_for("workspace", team_id=team_id))
    task = fetch_one("SELECT task_id FROM tasks WHERE task_id = %s AND team_id = %s", (task_id, team_id))
    if not task:
        abort(404)

    title = normalise_text(request.form.get("title"))
    description = normalise_text(request.form.get("description"))
    assignee = request.form.get("assigned_to", type=int)
    priority = request.form.get("priority", "Medium")
    status = request.form.get("status", "Pending")
    deadline = normalise_text(request.form.get("deadline")) or None
    if len(title) < 3 or priority not in ALLOWED_PRIORITIES or status not in ALLOWED_STATUSES or not assignee or not get_membership(team_id, assignee):
        flash("Please check the task details and try again.", "danger")
    else:
        execute(
            """UPDATE tasks SET title=%s, description=%s, assigned_to=%s, priority=%s,
                      status=%s, deadline=%s, updated_at=CURRENT_TIMESTAMP
               WHERE task_id=%s AND team_id=%s""",
            (title, description, assignee, priority, status, deadline, task_id, team_id),
        )
        flash("Task details updated.", "success")
    return redirect(url_for("workspace", team_id=team_id))


@app.post("/teams/<int:team_id>/tasks/<int:task_id>/delete")
@login_required
def delete_task(team_id, task_id):
    if not get_membership(team_id):
        abort(403)
    if not leader_required(team_id):
        return redirect(url_for("workspace", team_id=team_id))
    execute("DELETE FROM tasks WHERE task_id = %s AND team_id = %s", (task_id, team_id))
    flash("Task deleted.", "info")
    return redirect(url_for("workspace", team_id=team_id))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        name = normalise_text(request.form.get("full_name"))
        email = normalise_text(request.form.get("email")).lower()
        if len(name) < 2 or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
            flash("Please enter a valid name and email address.", "danger")
        elif fetch_one("SELECT user_id FROM users WHERE email = %s AND user_id <> %s", (email, user["user_id"])):
            flash("That email address is already in use.", "danger")
        else:
            execute("UPDATE users SET full_name=%s, email=%s WHERE user_id=%s", (name, email, user["user_id"]))
            flash("Profile updated successfully.", "success")
            return redirect(url_for("profile"))
    refreshed = fetch_one("SELECT user_id, full_name, email, created_at FROM users WHERE user_id = %s", (user["user_id"],))
    return render_template("profile.html", profile_user=refreshed)


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, title="Access denied", message="You do not have permission to view this workspace or perform this action."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, title="Page not found", message="The page you are looking for may have moved or no longer exists."), 404


@app.errorhandler(Error)
def database_error(error):
    app.logger.exception("Database error: %s", error)
    return render_template(
        "error.html", code=500, title="Database connection issue",
        message="CollabSpace could not connect to MySQL. Check your .env settings and make sure schema.sql has been imported.",
    ), 500


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "true").lower() == "true")
