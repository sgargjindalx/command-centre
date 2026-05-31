"""JSL Command Centre — standalone Flask app."""
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, render_template, request

import config
import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = config.SECRET_KEY

# Apply schema at import time so gunicorn workers pick it up.
# Wrapped in try/except so the app starts even if the DB isn't
# reachable yet — Railway wires DATABASE_URL after the build step.
try:
    database.apply_schema()
except Exception as _e:
    logger.warning("apply_schema() skipped at startup: %s", _e)


# ── Auth helpers ──────────────────────────────────────────────────────
def _check_api_key():
    """Return True if the request carries a valid API key (or no key is configured)."""
    if not config.API_KEY:
        return True   # no key configured → open (dev mode)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == config.API_KEY
    return request.headers.get("X-Api-Key", "") == config.API_KEY


def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _check_api_key():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Web UI ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    with database.get_cursor() as cur:
        cur.execute(
            """SELECT p.name, p.description, p.url, p.status,
                      count(pu.id) AS update_count,
                      max(pu.update_date) AS last_update
               FROM projects p
               LEFT JOIN project_updates pu ON pu.project_id = p.id
               WHERE p.status = 'active'
               GROUP BY p.id
               ORDER BY p.name""")
        projects = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """SELECT p.name AS project_name, pu.update_text, pu.update_date,
                      pu.source, pu.created_at
               FROM project_updates pu
               JOIN projects p ON p.id = pu.project_id
               ORDER BY pu.update_date DESC, pu.created_at DESC
               LIMIT 50""")
        updates = [dict(r) for r in cur.fetchall()]

    for u in updates:
        if hasattr(u.get("update_date"), "isoformat"):
            u["update_date"] = u["update_date"].isoformat()
        if isinstance(u.get("created_at"), datetime):
            u["created_at"] = u["created_at"].isoformat()

    return render_template("index.html", projects=projects, updates=updates)


# ── Health ────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    # Always return 200 so Railway's healthcheck passes even when the
    # DB is temporarily unreachable (e.g. during initial provisioning).
    ok = database.healthcheck()
    return jsonify({
        "status": "ok" if ok else "degraded",
        "db": "ok" if ok else "fail",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }), 200


# ── GET /api/projects ─────────────────────────────────────────────────
@app.route("/api/projects")
def api_projects():
    with database.get_cursor() as cur:
        cur.execute(
            """SELECT id, name, description, url, status, created_at
               FROM projects ORDER BY name""")
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
    return jsonify(rows)


# ── GET /api/project-updates ──────────────────────────────────────────
@app.route("/api/project-updates")
def api_project_updates():
    project_name = request.args.get("project", "").strip()
    limit = min(int(request.args.get("limit", 20)), 500)
    with database.get_cursor() as cur:
        if project_name:
            cur.execute(
                """SELECT pu.id, p.name AS project_name, pu.update_text,
                          pu.update_date, pu.source, pu.created_at
                   FROM project_updates pu
                   JOIN projects p ON p.id = pu.project_id
                   WHERE p.name = %s
                   ORDER BY pu.update_date DESC, pu.created_at DESC
                   LIMIT %s""",
                (project_name, limit))
        else:
            cur.execute(
                """SELECT pu.id, p.name AS project_name, pu.update_text,
                          pu.update_date, pu.source, pu.created_at
                   FROM project_updates pu
                   JOIN projects p ON p.id = pu.project_id
                   ORDER BY pu.update_date DESC, pu.created_at DESC
                   LIMIT %s""",
                (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
        if hasattr(r.get("update_date"), "isoformat"):
            r["update_date"] = r["update_date"].isoformat()
    return jsonify(rows)


# ── POST /api/log-update ──────────────────────────────────────────────
@app.route("/api/log-update", methods=["POST"])
@require_api_key
def api_log_update():
    body         = request.get_json(silent=True) or {}
    project_name = (body.get("project_name") or "").strip()
    update_text  = (body.get("update_text")  or "").strip()
    update_date  = (body.get("update_date")  or "").strip()
    source       = (body.get("source")       or "claude_project").strip()

    if not project_name:
        return jsonify({"error": "project_name required"}), 400
    if not update_text:
        return jsonify({"error": "update_text required"}), 400
    if not update_date:
        return jsonify({"error": "update_date required (YYYY-MM-DD)"}), 400

    with database.get_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": f"project '{project_name}' not found"}), 404
        project_id = row["id"]
        cur.execute(
            """INSERT INTO project_updates (project_id, update_text, update_date, source)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (project_id, update_text, update_date, source))
        new_id = cur.fetchone()["id"]

    logger.info("log-update: project=%r id=%d date=%s source=%s",
                project_name, new_id, update_date, source)
    return jsonify({"ok": True, "id": new_id, "project_name": project_name}), 201


# ── Boot ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    database.apply_schema()
    app.run(host="0.0.0.0", port=config.PORT, debug=not config.IS_PROD)
