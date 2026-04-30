import os
import json
import uuid
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

import database as db
from analysis import analyze_report, analyze_combined_report, detect_platform, load_file

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "static", "uploads"))
ALLOWED_REPORT_EXT = {"csv", "xlsx", "xls"}
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
ALLOWED_LOGO_EXT = {"png", "jpg", "jpeg", "svg", "webp"}

for _d in ["reports", "images", "logos"]:
    os.makedirs(os.path.join(UPLOAD_DIR, _d), exist_ok=True)

db.init_db()


def allowed_file(filename, allowed):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


# ── Dashboard ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    stats = db.get_stats()
    return render_template("index.html", stats=stats)


# ── Clients ───────────────────────────────────────────────────────────────

@app.route("/clients")
def clients_list():
    clients = db.get_all_clients()
    return render_template("clients/list.html", clients=clients)


@app.route("/clients/new", methods=["GET", "POST"])
def clients_new():
    if request.method == "POST":
        name = request.form["name"].strip()
        primary = request.form.get("primary_color", "#1a73e8")
        secondary = request.form.get("secondary_color", "#ffffff")
        accent = request.form.get("accent_color", "#34a853")
        text = request.form.get("text_color", "#1a1a1a")

        logo_path = None
        if "logo" in request.files and request.files["logo"].filename:
            logo = request.files["logo"]
            if allowed_file(logo.filename, ALLOWED_LOGO_EXT):
                fn = f"logo_{uuid.uuid4().hex}_{secure_filename(logo.filename)}"
                logo.save(os.path.join(UPLOAD_DIR, "logos", fn))
                logo_path = f"uploads/logos/{fn}"

        client_id = db.create_client(name, logo_path, primary, secondary, accent, text)
        return redirect(url_for("clients_list"))

    return render_template("clients/new.html")


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
def clients_edit(client_id):
    client = db.get_client(client_id)
    if not client:
        abort(404)

    if request.method == "POST":
        name = request.form["name"].strip()
        primary = request.form.get("primary_color", client["primary_color"])
        secondary = request.form.get("secondary_color", client["secondary_color"])
        accent = request.form.get("accent_color", client["accent_color"])
        text = request.form.get("text_color", client["text_color"])

        logo_path = client["logo_path"]
        if "logo" in request.files and request.files["logo"].filename:
            logo = request.files["logo"]
            if allowed_file(logo.filename, ALLOWED_LOGO_EXT):
                fn = f"logo_{uuid.uuid4().hex}_{secure_filename(logo.filename)}"
                logo.save(os.path.join(UPLOAD_DIR, "logos", fn))
                logo_path = f"uploads/logos/{fn}"

        db.update_client(client_id, name, logo_path, primary, secondary, accent, text)
        return redirect(url_for("clients_list"))

    reports = db.get_client_reports(client_id)
    return render_template("clients/edit.html", client=client, reports=reports)


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def clients_delete(client_id):
    db.delete_client(client_id)
    return redirect(url_for("clients_list"))


# ── Reports ───────────────────────────────────────────────────────────────

@app.route("/reports")
def reports_list():
    reports = db.get_all_reports()
    return render_template("reports/list.html", reports=reports)


@app.route("/reports/new", methods=["GET", "POST"])
def reports_new():
    clients = db.get_all_clients()
    if request.method == "GET":
        return render_template("reports/upload.html", clients=clients)

    meta_f   = request.files.get("meta_file")
    google_f = request.files.get("google_file")

    has_meta   = meta_f   and meta_f.filename   and allowed_file(meta_f.filename,   ALLOWED_REPORT_EXT)
    has_google = google_f and google_f.filename and allowed_file(google_f.filename, ALLOWED_REPORT_EXT)

    if not has_meta and not has_google:
        return render_template("reports/upload.html", clients=clients, error="יש להעלות לפחות קובץ אחד")

    client_id = request.form.get("client_id")
    title = request.form.get("title", "").strip() or "דוח חדש"

    meta_path = google_path = None
    source_parts = []

    if has_meta:
        fn = f"{uuid.uuid4().hex}_{secure_filename(meta_f.filename)}"
        meta_path = os.path.join(UPLOAD_DIR, "reports", fn)
        meta_f.save(meta_path)
        source_parts.append(f"uploads/reports/{fn}")

    if has_google:
        fn = f"{uuid.uuid4().hex}_{secure_filename(google_f.filename)}"
        google_path = os.path.join(UPLOAD_DIR, "reports", fn)
        google_f.save(google_path)
        source_parts.append(f"uploads/reports/{fn}")

    try:
        metrics, slides = analyze_combined_report(meta_path, google_path)
    except Exception as e:
        for p in [meta_path, google_path]:
            if p and os.path.exists(p):
                os.remove(p)
        return render_template("reports/upload.html", clients=clients, error=f"שגיאה בניתוח הקובץ: {str(e)}")

    if slides and slides[0].get("type") == "cover":
        slides[0]["title"] = title

    # Assign uploaded slide images by type order
    # Slots: img_cover → cover, img_meta → first kpi_slide, img_google → second kpi_slide, img_rec → recommendations_slide
    slot_map = {"img_cover": "cover", "img_meta": "kpi_slide:0", "img_google": "kpi_slide:1", "img_rec": "recommendations_slide"}
    type_indices = {}  # track how many of each type we've seen
    for slide in slides:
        t = slide.get("type", "")
        type_indices[t] = type_indices.get(t, 0)
        for field, target in slot_map.items():
            f = request.files.get(field)
            if not f or not f.filename or not allowed_file(f.filename, ALLOWED_IMAGE_EXT):
                continue
            if ":" in target:
                ttype, tnum = target.split(":")
                if t == ttype and type_indices[t] == int(tnum):
                    fn = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
                    f.save(os.path.join(UPLOAD_DIR, "images", fn))
                    slide["image_url"] = f"/static/uploads/images/{fn}"
            elif t == target and type_indices[t] == 0:
                fn = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
                f.save(os.path.join(UPLOAD_DIR, "images", fn))
                slide["image_url"] = f"/static/uploads/images/{fn}"
        type_indices[t] += 1

    platform = metrics.get("platform", "meta")
    date_range = (metrics.get("date_range") or
                  (metrics.get("meta", {}) or {}).get("date_range", ""))
    content  = json.dumps({"slides": slides}, ensure_ascii=False)
    raw_data = json.dumps(metrics, ensure_ascii=False)

    report_id = db.create_report(
        client_id=int(client_id) if client_id else None,
        title=title,
        platform=platform,
        date_range=date_range,
        source_file=", ".join(source_parts),
        content=content,
        raw_data=raw_data
    )
    return redirect(url_for("reports_edit", report_id=report_id))


@app.route("/reports/<int:report_id>/edit")
def reports_edit(report_id):
    report = db.get_report(report_id)
    if not report:
        abort(404)
    images = db.get_report_images(report_id)
    content = json.loads(report["content"]) if report["content"] else {"slides": []}
    return render_template("reports/editor.html", report=report, content=content, images=images)


@app.route("/reports/<int:report_id>/save", methods=["POST"])
def reports_save(report_id):
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "no data"}), 400
    # accept either {slides:[...]} or {blocks:[...]} (legacy)
    if "slides" in data:
        content = json.dumps({"slides": data["slides"]}, ensure_ascii=False)
    elif "blocks" in data:
        content = json.dumps({"blocks": data["blocks"]}, ensure_ascii=False)
    else:
        content = json.dumps(data, ensure_ascii=False)
    db.update_report_content(report_id, content)
    return jsonify({"ok": True})


@app.route("/reports/<int:report_id>/publish", methods=["POST"])
def reports_publish(report_id):
    report = db.get_report(report_id)
    if not report:
        abort(404)
    token = report["embed_token"] or uuid.uuid4().hex
    db.publish_report(report_id, token)
    embed_url = url_for("embed_view", token=token, _external=True)
    iframe_code = f'<iframe src="{embed_url}" width="100%" height="900" frameborder="0" allowfullscreen></iframe>'
    return jsonify({"ok": True, "token": token, "embed_url": embed_url, "iframe_code": iframe_code})


@app.route("/reports/<int:report_id>/unpublish", methods=["POST"])
def reports_unpublish(report_id):
    db.update_report_content(report_id, db.get_report(report_id)["content"])
    with db.get_db() as conn:
        conn.execute("UPDATE reports SET status='draft' WHERE id=?", (report_id,))
    return jsonify({"ok": True})


@app.route("/reports/<int:report_id>/view")
def reports_view(report_id):
    report = db.get_report(report_id)
    if not report:
        abort(404)
    content = json.loads(report["content"]) if report["content"] else {"slides": []}
    images = db.get_report_images(report_id)
    return render_template("reports/view.html", report=report, content=content, images=images, embed=False)


@app.route("/reports/<int:report_id>/slides/<int:slide_idx>/image", methods=["POST"])
def upload_slide_image(report_id, slide_idx):
    f = request.files.get("image")
    if not f or not allowed_file(f.filename, ALLOWED_IMAGE_EXT):
        return jsonify({"ok": False, "error": "קובץ לא תקין"}), 400
    fn = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
    f.save(os.path.join(UPLOAD_DIR, "images", fn))
    url = f"/static/uploads/images/{fn}"

    report = db.get_report(report_id)
    if not report:
        abort(404)
    content = json.loads(report["content"]) if report["content"] else {"slides": []}
    slides = content.get("slides", [])
    if 0 <= slide_idx < len(slides):
        slides[slide_idx]["image_url"] = url
        content["slides"] = slides
        db.update_report_content(report_id, json.dumps(content, ensure_ascii=False))
    return jsonify({"ok": True, "url": url})


@app.route("/reports/<int:report_id>/delete", methods=["POST"])
def reports_delete(report_id):
    db.delete_report(report_id)
    return redirect(url_for("reports_list"))


# ── Images ────────────────────────────────────────────────────────────────

@app.route("/reports/<int:report_id>/images", methods=["POST"])
def upload_image(report_id):
    f = request.files.get("image")
    if not f or not allowed_file(f.filename, ALLOWED_IMAGE_EXT):
        return jsonify({"ok": False, "error": "קובץ לא תקין"}), 400
    caption = request.form.get("caption", "")
    fn = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
    f.save(os.path.join(UPLOAD_DIR, "images", fn))
    rel_path = f"uploads/images/{fn}"
    img_id = db.add_report_image(report_id, rel_path, caption)
    return jsonify({"ok": True, "id": img_id, "url": f"/static/{rel_path}", "caption": caption})


@app.route("/images/<int:image_id>/delete", methods=["POST"])
def delete_image(image_id):
    path = db.delete_image(image_id)
    if path:
        full = os.path.join(BASE_DIR, "static", path)
        if os.path.exists(full):
            os.remove(full)
    return jsonify({"ok": True})


# ── iFrame embed ──────────────────────────────────────────────────────────

@app.route("/embed/<token>")
def embed_view(token):
    report = db.get_report_by_token(token)
    if not report:
        abort(404)
    content = json.loads(report["content"]) if report["content"] else {"slides": []}
    images = db.get_report_images(report["id"])
    return render_template("reports/view.html", report=report, content=content, images=images, embed=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", debug=debug, port=port)
