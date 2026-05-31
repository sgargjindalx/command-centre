"""SG Cmd Centre — standalone Flask app."""
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

# Track whether the schema has been applied yet.
# Retried on the first successful DB connection if startup failed.
_schema_applied = False


def _try_apply_schema():
    global _schema_applied
    if _schema_applied:
        return True
    if not config.DATABASE_URL:
        logger.warning("DATABASE_URL not set — skipping schema apply")
        return False
    try:
        database.apply_schema()
        _schema_applied = True
        logger.info("Schema applied successfully")
        return True
    except Exception as e:
        logger.warning("apply_schema() failed: %s", e)
        return False


# Attempt schema on startup (gunicorn import); failure is non-fatal.
_try_apply_schema()


# ── Auth helpers ──────────────────────────────────────────────────────
def _check_api_key():
    if not config.API_KEY:
        return True
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


# ── Health — always 200 ───────────────────────────────────────────────
@app.route("/health")
def health():
    db_ok = database.healthcheck()
    if db_ok:
        _try_apply_schema()   # retry schema if startup missed it
    return jsonify({
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "fail",
        "database_url_set": bool(config.DATABASE_URL),
        "schema_applied": _schema_applied,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }), 200


# ── Web UI ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if not config.DATABASE_URL:
        return render_template("index.html", projects=[], updates=[],
                               error="DATABASE_URL is not configured. "
                                     "Add a PostgreSQL service in Railway and link DATABASE_URL.")
    _try_apply_schema()
    try:
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
    except Exception as e:
        logger.exception("/ DB error")
        return render_template("index.html", projects=[], updates=[],
                               error=f"Database error: {e}")

    for u in updates:
        if hasattr(u.get("update_date"), "isoformat"):
            u["update_date"] = u["update_date"].isoformat()
        if isinstance(u.get("created_at"), datetime):
            u["created_at"] = u["created_at"].isoformat()

    return render_template("index.html", projects=projects, updates=updates, error=None)


# ── GET /api/projects ─────────────────────────────────────────────────
@app.route("/api/projects")
def api_projects():
    if not config.DATABASE_URL:
        return jsonify({"error": "DATABASE_URL not configured"}), 503
    _try_apply_schema()
    try:
        with database.get_cursor() as cur:
            cur.execute(
                "SELECT id, name, description, url, status, created_at "
                "FROM projects ORDER BY name")
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.exception("/api/projects DB error")
        return jsonify({"error": "database unavailable", "detail": str(e)}), 503
    for r in rows:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
    return jsonify(rows)


# ── GET /api/action-items ────────────────────────────────────────────
@app.route("/api/action-items")
def api_action_items():
    if not config.DATABASE_URL:
        return jsonify({"error": "DATABASE_URL not configured"}), 503
    _try_apply_schema()
    try:
        with database.get_cursor() as cur:
            cur.execute(
                """SELECT ai.id, p.name AS project_name, ai.action_text,
                          ai.detail_text, ai.done, ai.created_at
                   FROM action_items ai
                   JOIN projects p ON p.id = ai.project_id
                   ORDER BY ai.done ASC, ai.created_at ASC""")
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.exception("/api/action-items DB error")
        return jsonify({"error": "database unavailable", "detail": str(e)}), 503
    for r in rows:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
    return jsonify(rows)


# ── PATCH /api/action-items/<id> ──────────────────────────────────────
@app.route("/api/action-items/<int:item_id>", methods=["PATCH"])
def api_action_item_patch(item_id):
    if not config.DATABASE_URL:
        return jsonify({"error": "DATABASE_URL not configured"}), 503
    body = request.get_json(silent=True) or {}
    if "done" not in body:
        return jsonify({"error": "done (bool) required"}), 400
    try:
        with database.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE action_items SET done=%s WHERE id=%s RETURNING id, done",
                (bool(body["done"]), item_id))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
    except Exception as e:
        logger.exception("/api/action-items PATCH DB error")
        return jsonify({"error": "database unavailable", "detail": str(e)}), 503
    return jsonify({"ok": True, "id": row["id"], "done": row["done"]})


# ── GET /api/updates (alias for project-updates) ──────────────────────
@app.route("/api/updates")
def api_updates():
    return api_project_updates()


# ── GET /api/project-updates ──────────────────────────────────────────
@app.route("/api/project-updates")
def api_project_updates():
    if not config.DATABASE_URL:
        return jsonify({"error": "DATABASE_URL not configured"}), 503
    _try_apply_schema()
    project_name = request.args.get("project", "").strip()
    limit = min(int(request.args.get("limit", 20)), 500)
    try:
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
    except Exception as e:
        logger.exception("/api/project-updates DB error")
        return jsonify({"error": "database unavailable", "detail": str(e)}), 503
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
    if not config.DATABASE_URL:
        return jsonify({"error": "DATABASE_URL not configured"}), 503
    _try_apply_schema()
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

    try:
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
    except Exception as e:
        logger.exception("/api/log-update DB error")
        return jsonify({"error": "database unavailable", "detail": str(e)}), 503

    logger.info("log-update: project=%r id=%d date=%s source=%s",
                project_name, new_id, update_date, source)
    return jsonify({"ok": True, "id": new_id, "project_name": project_name}), 201


# ── Boot ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _try_apply_schema()
    app.run(host="0.0.0.0", port=config.PORT, debug=not config.IS_PROD)
