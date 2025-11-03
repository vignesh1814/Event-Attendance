from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    flash,
)
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os

DB = "database.db"
app = Flask(__name__)
app.secret_key = "dev-secret-change-this"  # change for production


def get_db():
    conn = sqlite3.connect(DB, timeout=20)  # Add timeout for busy database
    conn.row_factory = sqlite3.Row
    return conn

def get_db_connection():
    """Context manager for database connections"""
    conn = None
    try:
        conn = get_db()
        return conn
    except Exception as e:
        if conn:
            conn.close()
        raise e


def init_db():
    if os.path.exists(DB):
        print("📊  DB already exists!!")
        return
    conn = get_db()
    cur = conn.cursor()
    # users: role is 'conductor' or 'hod'
    cur.executescript("""
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        branch TEXT
    );
    CREATE TABLE students (
        roll TEXT PRIMARY KEY,
        name TEXT,
        branch TEXT
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        location TEXT,
        when_dt TEXT,
        creator_id INTEGER,
        created_at TEXT
    );
    CREATE TABLE attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER,
        roll TEXT,
        scanned_at TEXT,
        conductor_id INTEGER,
        status TEXT DEFAULT 'Pending', -- Pending / Approved / Rejected
        hod_id INTEGER,
        hod_action_at TEXT
    );
    """)
    # seed users
    cur.execute(
        "INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",
        (
            "Event Conductor",
            "conductor@example.com",
            "pass",
            "conductor",
        ),
    )
    cur.execute(
        "INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",
        ("Head of Dept", "hod@example.com", "pass", "hod"),
    )
    conn.commit()
    conn.close()
    print("DB initialized at", DB)


init_db()

### Authentication helpers


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    try:
        with get_db_connection() as conn:
            u = conn.execute(
                "SELECT id,name,email,role FROM users WHERE id=?", (uid,)
            ).fetchone()
            return u
    except sqlite3.OperationalError as e:
        print(f"Database error in current_user: {e}")
        return None


### Routes


@app.route("/")
def index():
    user = current_user()
    if user:
        return render_template(("index.html"))
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        pw = request.form["password"]
        try:
            with get_db_connection() as conn:
                row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if row and (row["password_hash"], pw):
                    session["user_id"] = row["id"]
                    flash("Logged in successfully.", "success")
                    return redirect(url_for("dashboard"))
                flash("Invalid credentials", "danger")
        except sqlite3.OperationalError as e:
            flash("Database error. Please try again.", "danger")
            print(f"Database error in login: {e}")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]
        role = request.form["role"]
        branch = request.form["branch"]
        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect(url_for("register"))
        try:
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT INTO users (name,email,password_hash,role,branch) VALUES (?,?,?,?,?)",
                    (name, email, password, role, branch),
                )
                conn.commit()
                flash("Registered successfully! Please log in.", "success")
                return redirect(url_for("login"))
        except sqlite3.OperationalError as e:
            flash("Database error. Please try again.", "danger")
            print(f"Database error in register: {e}")
        except sqlite3.IntegrityError:
            flash("Email already registered!", "danger")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    try:
        with get_db_connection() as conn:
            if user["role"] == "conductor":
                events = conn.execute(
                    "SELECT * FROM events WHERE creator_id=? ORDER BY id DESC", (user["id"],)
                ).fetchall()
                print(events)
                return render_template("conductor_dashboard.html", user=user, events=events)
            elif user["role"] == "student":
                print("Student")
            elif user["role"] == "hod":
                # hod sees all events
                events = conn.execute("SELECT * FROM events ORDER BY id DESC").fetchall()
                print("here: ",events)
                return render_template("hod_dashboard.html", user=user, events=events)
    except sqlite3.OperationalError as e:
        flash("Database error. Please try again.", "danger")
        print(f"Database error in dashboard: {e}")
        return redirect(url_for("index"))


@app.route("/create_event", methods=["GET", "POST"])
def create_event():
    user = current_user()
    if not user or user["role"] != "conductor":
        return redirect(url_for("login"))
    if request.method == "POST":
        title = request.form["title"]
        desc = request.form["description"]
        location = request.form["location"]
        when_dt = request.form["when_dt"]
        conn = get_db()
        conn.execute(
            "INSERT INTO events (title,description,location,when_dt,creator_id,created_at) VALUES (?,?,?,?,?,?)",
            (title, desc, location, when_dt, user["id"], datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("dashboard"))
    return render_template("create_event.html", user=user)


@app.route("/event/<int:event_id>")
def view_event(event_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    try:
        with get_db_connection() as conn:
            event = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not event:
                return "Event not found", 404
            # get attendance rows
            rows = conn.execute(
                "SELECT a.*, s.name as student_name, s.branch as branch FROM attendance a LEFT JOIN students s ON a.roll=s.roll WHERE event_id=? ORDER BY scanned_at DESC",
                (event_id,),
            ).fetchall()
            # Different template view for conductor vs hod
            if user["role"] == "conductor":
                return render_template(
                    "conductor_event.html", user=user, event=event, rows=rows
                )
            else:
                return render_template("hod_event.html", user=user, event=event, rows=rows)
    except sqlite3.OperationalError as e:
        flash("Database error. Please try again.", "danger")
        print(f"Database error in view_event: {e}")
        return redirect(url_for("dashboard"))


# AJAX endpoint used by scanner to fetch student details by roll
@app.route("/scan_lookup", methods=["POST"])
def scan_lookup():
    user = current_user()
    if not user or user["role"] != "conductor":
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    roll = data.get("roll", "").strip()
    if not roll:
        return jsonify({"error": "no roll"}), 400
    try:
        with get_db_connection() as conn:
            student = conn.execute(
                "SELECT roll,name,branch FROM students WHERE roll=?", (roll,)
            ).fetchone()
            if student:
                # return details so they can be Reviewed before adding to "Pending list"
                return jsonify(
                    {
                        "roll": student["roll"],
                        "name": student["name"],
                        "branch": student["branch"],
                    }
                )
            else:
                return jsonify({"roll": roll, "name": None, "branch": None})
    except sqlite3.OperationalError as e:
        print(f"Database error in scan_lookup: {e}")
        return jsonify({"error": "database error"}), 500


# When conductor confirms a scanned student locally, save it to DB with scanned_at and Pending status
@app.route("/add_scan", methods=["POST"])
def add_scan():
    user = current_user()
    if not user or user["role"] != "conductor":
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json()
    event_id = payload.get("event_id")
    roll = payload.get("roll")
    scanned_at = datetime.utcnow().isoformat()
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO attendance (event_id,roll,scanned_at,conductor_id,status) VALUES (?,?,?,?,?)",
                (event_id, roll, scanned_at, user["id"], "Pending"),
            )
            conn.commit()
            return jsonify({"ok": True, "scanned_at": scanned_at})
    except sqlite3.OperationalError as e:
        print(f"Database error in add_scan: {e}")
        return jsonify({"error": "database error"}), 500


# HOD Approves/Rejects attendance
@app.route("/hod_action", methods=["POST"])
def hod_action():
    user = current_user()
    if not user or user["role"] != "hod":
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json()
    attendance_id = payload.get("attendance_id")
    action = payload.get("action")  # Approve or Reject
    t = datetime.utcnow().isoformat()
    try:
        with get_db_connection() as conn:
            if action == "Approved":
                conn.execute(
                    "UPDATE attendance SET status='Approved', hod_id=?, hod_action_at=? WHERE id=?",
                    (user["id"], t, attendance_id),
                )
            elif action == "Pending":
                conn.execute(
                    "UPDATE attendance SET status='Pending', hod_id=?, hod_action_at=? WHERE id=?",
                    (user["id"], t, attendance_id),
                )
            elif action == "Rejected":
                conn.execute(
                    "UPDATE attendance SET status='Rejected', hod_id=?, hod_action_at=? WHERE id=?",
                    (user["id"], t, attendance_id),
                )
            conn.commit()
            return jsonify({"ok": True})
    except sqlite3.OperationalError as e:
        print(f"Database error in hod_action: {e}")
        return jsonify({"error": "database error"}), 500


if __name__ == "__main__":
    app.run(debug=True,host='0.0.0.0',port=4112)
