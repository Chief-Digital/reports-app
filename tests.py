"""
סוויטת בדיקות מקיפה ל-ReportAI
מריץ: python3 tests.py
"""
import sys, os, io, json, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

# ── helpers ────────────────────────────────────────────────────────────────────

def make_meta_csv(encoding="utf-8", bom=False) -> bytes:
    content = (
        "Reporting starts,Reporting ends,Campaign name,Campaign delivery,"
        "Results,Result indicator,Cost per results,Results ROAS,Result value indicator,"
        "Amount spent (ILS),Impressions,Reach,Results value,Link clicks,"
        "Unique link clicks,CTR (link click-through rate),"
        "CPM (cost per 1,000 impressions) (ILS),Starts\n"
        "2026-02-01,2026-02-28,Best Seller,active,27,Purchase,137,5.52,,"
        "3717.58,120000,85000,20520,1500,1300,1.25,45.2,0\n"
        "2026-02-01,2026-02-28,Awareness Campaign,active,82538,Post engagement,0.0025,,"
        ",206.17,500000,300000,0,200,180,0.04,4.1,0\n"
        "2026-02-01,2026-02-28,Inactive Campaign,inactive,,,,,,"
        "0,0,0,0,0,0,0,0,0\n"
    )
    raw = content.encode(encoding)
    if bom:
        bom_bytes = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff",
                     "utf-8": b"\xef\xbb\xbf"}.get(encoding, b"")
        return bom_bytes + raw
    return raw


def make_google_csv(encoding="utf-8") -> bytes:
    content = (
        "Campaign,Cost,Conversions,Conv. value / cost,Clicks,Impressions,CTR,Avg. CPC\n"
        "Brand Search,2500.00,18,4.2,800,12000,6.67,3.125\n"
        "Generic Search,1800.00,10,2.8,1200,30000,4.0,1.5\n"
        "Remarketing Display,700.00,5,1.9,300,80000,0.375,2.33\n"
    )
    return content.encode(encoding)


def write_tmp(data: bytes, suffix=".csv") -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return f.name


PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []


def check(name, fn):
    try:
        fn()
        print(f"  {PASS} {name}")
        results.append((name, True, None))
    except Exception as e:
        print(f"  {FAIL} {name}: {e}")
        results.append((name, False, str(e)))


# ══════════════════════════════════════════════════════════════════════════════
# 1. FILE LOADING
# ══════════════════════════════════════════════════════════════════════════════
print("\n📁 1. טעינת קבצים (load_file)")

from analysis import load_file, detect_platform, parse_meta_file, parse_google_ads_file

def test_utf8():
    p = write_tmp(make_meta_csv("utf-8"))
    try:
        df = load_file(p)
        assert len(df) == 3, f"expected 3 rows, got {len(df)}"
    finally:
        os.unlink(p)

def test_utf8_bom():
    p = write_tmp(make_meta_csv("utf-8", bom=True))
    try:
        df = load_file(p)
        assert len(df) == 3
    finally:
        os.unlink(p)

def test_utf16_le_bom():
    raw = b"\xff\xfe" + make_meta_csv("utf-8").decode().encode("utf-16-le")
    p = write_tmp(raw)
    try:
        df = load_file(p)
        assert len(df) == 3
        assert "Campaign name" in df.columns, f"missing Campaign name, got {list(df.columns)[:3]}"
    finally:
        os.unlink(p)

def test_utf16_be_bom():
    raw = b"\xfe\xff" + make_meta_csv("utf-8").decode().encode("utf-16-be")
    p = write_tmp(raw)
    try:
        df = load_file(p)
        assert len(df) == 3
    finally:
        os.unlink(p)

def test_latin1():
    p = write_tmp(make_google_csv("latin-1"))
    try:
        df = load_file(p)
        assert len(df) == 3
    finally:
        os.unlink(p)

def test_excel():
    import pandas as pd
    content = {
        "Campaign name": ["Best Seller", "Awareness"],
        "Amount spent (ILS)": [3717.58, 206.17],
        "Results": [27, 82538],
        "Results ROAS": [5.52, None],
        "Impressions": [120000, 500000],
        "Reach": [85000, 300000],
        "Link clicks": [1500, 200],
        "CTR (link click-through rate)": [1.25, 0.04],
        "CPM (cost per 1,000 impressions) (ILS)": [45.2, 4.1],
    }
    df_orig = pd.DataFrame(content)
    p = write_tmp(b"", suffix=".xlsx")
    df_orig.to_excel(p, index=False)
    try:
        df = load_file(p)
        assert len(df) == 2
        assert "Amount spent (ILS)" in df.columns
    finally:
        os.unlink(p)

def test_empty_file():
    p = write_tmp(b"col1,col2\n")
    try:
        df = load_file(p)
    finally:
        os.unlink(p)

def test_meta_with_metadata_rows():
    """Meta sometimes prepends report metadata before the real CSV header."""
    content = (
        "Report Name: De-ase Campaigns\n"
        "Date range: 2026-02-01 - 2026-02-28\n"
        "Account: De-ase\n"
        "\n"
        "Campaign name,Amount spent (ILS),Results,Results ROAS,Impressions,Reach,"
        "Link clicks,CTR (link click-through rate),CPM (cost per 1,000 impressions) (ILS)\n"
        "Best Seller,3717.58,27,5.52,120000,85000,1500,1.25,45.2\n"
        "Awareness,206.17,82538,,500000,300000,200,0.04,4.1\n"
    )
    p = write_tmp(content.encode("utf-8"))
    try:
        df = load_file(p)
        assert "Campaign name" in df.columns, f"missing Campaign name, cols={list(df.columns)[:4]}"
        assert len(df) == 2, f"expected 2 rows, got {len(df)}"
    finally:
        os.unlink(p)

def test_utf16_with_metadata_rows():
    """UTF-16 + metadata rows — the combination that caused the real error."""
    content = (
        "Report Name: De-ase Campaigns\n"
        "Date range: 2026-02-01 - 2026-02-28\n"
        "\n"
        "Campaign name,Amount spent (ILS),Results,Results ROAS,Impressions,Reach,"
        "Link clicks,CTR (link click-through rate),CPM (cost per 1,000 impressions) (ILS)\n"
        "Best Seller,3717.58,27,5.52,120000,85000,1500,1.25,45.2\n"
        "Awareness,206.17,82538,,500000,300000,200,0.04,4.1\n"
    )
    raw = b"\xff\xfe" + content.encode("utf-16-le")
    p = write_tmp(raw)
    try:
        df = load_file(p)
        assert "Campaign name" in df.columns, f"cols={list(df.columns)[:4]}"
        assert len(df) == 2
        m = parse_meta_file(df)
        assert m["total_spend"] > 0
    finally:
        os.unlink(p)

def test_windows_crlf():
    """Windows-style CRLF line endings."""
    content = "Campaign name,Amount spent (ILS),Results\r\nBest Seller,3717.58,27\r\nAwareness,206.17,5\r\n"
    p = write_tmp(content.encode("utf-8"))
    try:
        df = load_file(p)
        assert len(df) == 2
    finally:
        os.unlink(p)

check("UTF-8 ללא BOM", test_utf8)
check("UTF-8 עם BOM (0xEF BB BF)", test_utf8_bom)
check("UTF-16 LE עם BOM (0xFF FE) — ייצוא Meta נפוץ", test_utf16_le_bom)
check("UTF-16 BE עם BOM (0xFE FF)", test_utf16_be_bom)
check("Latin-1 / ISO-8859", test_latin1)
check("Excel (.xlsx)", test_excel)
check("קובץ ריק (רק headers)", test_empty_file)
check("Meta CSV עם שורות metadata בראש הקובץ", test_meta_with_metadata_rows)
check("UTF-16 + שורות metadata — הסיטואציה שגרמה לשגיאה", test_utf16_with_metadata_rows)
def test_google_hebrew_tsv():
    """Google Ads export from Hebrew UI — tab-separated with Hebrew headers + metadata row."""
    content = (
        "1 במרץ 2026 - 31 במרץ 2026\n"
        "סטטוס קמפיין\tקמפיין\tמחיר\tהמרות\tערך המרה/מחיר\tקליקים\tחשיפות\tשיעור קליקים (CTR)\tעלות ממוצעת לקליק\n"
        "פעיל\tPmax Sales\t5000.00\t18\t4.2\t800\t12000\t6.67\t3.125\n"
        "פעיל\tBrand Search\t2000.00\t10\t2.8\t500\t8000\t6.25\t4.0\n"
        "מושהה\tOld Campaign\t0.00\t0\t0\t0\t0\t0\t0\n"
    )
    p = write_tmp(content.encode("utf-8"))
    try:
        df = load_file(p)
        assert "Campaign" in df.columns or "קמפיין" in df.columns, f"cols={list(df.columns)[:5]}"
        platform = detect_platform(df)
        assert platform == "google_ads", f"expected google_ads, got {platform}"
        m = parse_google_ads_file(df)
        assert abs(m["total_spend"] - 7000.0) < 1, f"spend={m['total_spend']}"
        assert len(m["campaigns"]) == 3
        assert m["platform"] == "google_ads"
    finally:
        os.unlink(p)

check("CRLF (Windows line endings)", test_windows_crlf)
check("Google Ads בעברית + TSV + metadata — הסיטואציה האמיתית", test_google_hebrew_tsv)


# ══════════════════════════════════════════════════════════════════════════════
# 2. PLATFORM DETECTION
# ══════════════════════════════════════════════════════════════════════════════
print("\n🔍 2. זיהוי פלטפורמה (detect_platform)")
import pandas as pd

def test_detect_meta():
    p = write_tmp(make_meta_csv())
    try:
        df = load_file(p)
        assert detect_platform(df) == "meta", f"got {detect_platform(df)}"
    finally:
        os.unlink(p)

def test_detect_google():
    p = write_tmp(make_google_csv())
    try:
        df = load_file(p)
        assert detect_platform(df) == "google_ads", f"got {detect_platform(df)}"
    finally:
        os.unlink(p)

def test_detect_ambiguous():
    # A file with no recognizable columns → should return something, not crash
    df = pd.DataFrame({"col1": [1], "col2": [2]})
    result = detect_platform(df)
    assert result in ("meta", "google_ads")

check("זיהוי Meta מ-CSV", test_detect_meta)
check("זיהוי Google Ads מ-CSV", test_detect_google)
check("קולומנות לא מוכרות — לא קורס", test_detect_ambiguous)


# ══════════════════════════════════════════════════════════════════════════════
# 3. META PARSING
# ══════════════════════════════════════════════════════════════════════════════
print("\n📘 3. פרסינג Meta (parse_meta_file)")
from analysis import parse_meta_file

def test_meta_totals():
    p = write_tmp(make_meta_csv())
    try:
        df = load_file(p)
        m = parse_meta_file(df)
        assert abs(m["total_spend"] - 3923.75) < 1, f"spend={m['total_spend']}"
        assert m["total_results"] == 82565, f"results={m['total_results']}"
        assert m["platform"] == "meta"
    finally:
        os.unlink(p)

def test_meta_campaigns():
    p = write_tmp(make_meta_csv())
    try:
        df = load_file(p)
        m = parse_meta_file(df)
        assert len(m["campaigns"]) == 3, f"got {len(m['campaigns'])} campaigns"
        # inactive campaign should have 0 spend
        inactive = next(c for c in m["campaigns"] if "Inactive" in c["name"])
        assert inactive["spend"] == 0
    finally:
        os.unlink(p)

def test_meta_nan_safe():
    # NaN in numeric columns should not crash
    df = pd.DataFrame({
        "Campaign name": ["Camp A", "Camp B"],
        "Amount spent (ILS)": [1000.0, None],
        "Results": [10, None],
        "Results ROAS": [None, None],
        "Impressions": [50000, None],
        "Reach": [30000, None],
        "Link clicks": [500, None],
        "CTR (link click-through rate)": [1.0, None],
        "CPM (cost per 1,000 impressions) (ILS)": [20.0, None],
    })
    m = parse_meta_file(df)
    assert m["total_spend"] == 1000.0
    assert m["total_results"] == 10

def test_meta_date_range():
    p = write_tmp(make_meta_csv())
    try:
        df = load_file(p)
        m = parse_meta_file(df)
        assert "2026" in m["date_range"], f"date_range={m['date_range']}"
    finally:
        os.unlink(p)

check("סכומי Meta נכונים", test_meta_totals)
check("מספר קמפיינים + קמפיין לא פעיל עם spend=0", test_meta_campaigns)
check("NaN בעמודות מספריות לא קורס", test_meta_nan_safe)
check("תאריכים מחושבים נכון", test_meta_date_range)


# ══════════════════════════════════════════════════════════════════════════════
# 4. GOOGLE ADS PARSING
# ══════════════════════════════════════════════════════════════════════════════
print("\n🔴 4. פרסינג Google Ads (parse_google_ads_file)")
from analysis import parse_google_ads_file

def test_google_totals():
    p = write_tmp(make_google_csv())
    try:
        df = load_file(p)
        m = parse_google_ads_file(df)
        assert abs(m["total_spend"] - 5000.0) < 1, f"spend={m['total_spend']}"
        assert m["total_clicks"] == 2300, f"clicks={m['total_clicks']}"
        assert m["platform"] == "google_ads"
    finally:
        os.unlink(p)

def test_google_campaigns():
    p = write_tmp(make_google_csv())
    try:
        df = load_file(p)
        m = parse_google_ads_file(df)
        assert len(m["campaigns"]) == 3
        best = max(m["campaigns"], key=lambda c: c["roas"])
        assert best["roas"] == 4.2
    finally:
        os.unlink(p)

def test_google_nan_safe():
    df = pd.DataFrame({
        "Campaign": ["Camp A", "Camp B"],
        "Cost": [1000.0, None],
        "Conversions": [5, None],
        "Conv. value / cost": [3.5, None],
        "Clicks": [300, None],
        "Impressions": [10000, None],
        "CTR": [3.0, None],
        "Avg. CPC": [3.33, None],
    })
    m = parse_google_ads_file(df)
    assert m["total_spend"] == 1000.0
    assert m["total_clicks"] == 300

check("סכומי Google נכונים", test_google_totals)
check("הקמפיין עם ROAS הגבוה ביותר מזוהה", test_google_campaigns)
check("NaN ב-Google לא קורס", test_google_nan_safe)


# ══════════════════════════════════════════════════════════════════════════════
# 5. DATABASE
# ══════════════════════════════════════════════════════════════════════════════
print("\n🗄️  5. מסד נתונים (database)")
import database as db

# Use a fresh temp DB for tests
import database as db_module
orig_path = db_module.DB_PATH
db_module.DB_PATH = tempfile.mktemp(suffix=".db")

def test_db_init():
    db.init_db()

def test_create_client():
    db.init_db()
    cid = db.create_client("לקוח בדיקה", None, "#1a73e8", "#fff", "#34a853", "#111")
    assert isinstance(cid, int) and cid > 0
    c = db.get_client(cid)
    assert c["name"] == "לקוח בדיקה"
    assert c["primary_color"] == "#1a73e8"

def test_update_client():
    db.init_db()
    cid = db.create_client("לפני", None, "#000", "#fff", "#333", "#111")
    db.update_client(cid, "אחרי", None, "#ff0000", "#fff", "#00ff00", "#000")
    c = db.get_client(cid)
    assert c["name"] == "אחרי"
    assert c["primary_color"] == "#ff0000"

def test_delete_client():
    db.init_db()
    cid = db.create_client("למחיקה", None, "#000", "#fff", "#333", "#111")
    db.delete_client(cid)
    assert db.get_client(cid) is None

def test_create_report():
    db.init_db()
    cid = db.create_client("לקוח ל-report", None, "#000", "#fff", "#333", "#111")
    rid = db.create_report(cid, "דוח בדיקה", "meta", "2026-02-01 - 2026-02-28",
                           "uploads/test.csv", '{"slides":[]}', '{"platform":"meta"}')
    assert isinstance(rid, int) and rid > 0
    r = db.get_report(rid)
    assert r["title"] == "דוח בדיקה"
    assert r["platform"] == "meta"
    assert r["status"] == "draft"

def test_publish_report():
    db.init_db()
    cid = db.create_client("לקוח פרסום", None, "#000", "#fff", "#333", "#111")
    rid = db.create_report(cid, "דוח לפרסום", "meta", "", "", "{}", "{}")
    import uuid
    token = uuid.uuid4().hex
    db.publish_report(rid, token)
    r = db.get_report(rid)
    assert r["status"] == "published"
    assert r["embed_token"] == token
    r2 = db.get_report_by_token(token)
    assert r2 is not None and r2["id"] == rid

def test_report_images():
    db.init_db()
    cid = db.create_client("לקוח תמונות", None, "#000", "#fff", "#333", "#111")
    rid = db.create_report(cid, "דוח תמונות", "meta", "", "", "{}", "{}")
    iid = db.add_report_image(rid, "uploads/images/test.jpg", "כיתוב")
    imgs = db.get_report_images(rid)
    assert len(imgs) == 1
    assert imgs[0]["caption"] == "כיתוב"
    db.delete_image(iid)
    assert len(db.get_report_images(rid)) == 0

def test_stats():
    db.init_db()
    stats = db.get_stats()
    assert "clients" in stats
    assert "reports" in stats
    assert "published" in stats
    assert isinstance(stats["recent"], list)

check("init_db", test_db_init)
check("יצירת לקוח + קריאה", test_create_client)
check("עדכון לקוח", test_update_client)
check("מחיקת לקוח", test_delete_client)
check("יצירת דוח + קריאה", test_create_report)
check("פרסום דוח + embed_token", test_publish_report)
check("תמונות — הוספה, קריאה, מחיקה", test_report_images)
check("סטטיסטיקות dashboard", test_stats)

# Cleanup temp DB
os.unlink(db_module.DB_PATH)
db_module.DB_PATH = orig_path


# ══════════════════════════════════════════════════════════════════════════════
# 6. FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════
print("\n🌐 6. Flask routes")
from app import app as flask_app
flask_app.config["TESTING"] = True
client = flask_app.test_client()

def test_route_home():
    r = client.get("/")
    assert r.status_code == 200
    assert "ReportAI" in r.data.decode("utf-8")

def test_route_clients_list():
    r = client.get("/clients")
    assert r.status_code == 200

def test_route_clients_new_get():
    r = client.get("/clients/new")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "primary_color" in body
    assert "logo" in body

def test_route_reports_list():
    r = client.get("/reports")
    assert r.status_code == 200

def test_route_reports_new_get():
    r = client.get("/reports/new")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "meta_file" in body
    assert "google_file" in body

def test_route_404():
    r = client.get("/reports/999999/edit")
    assert r.status_code == 404

def test_route_reports_new_no_file():
    r = client.post("/reports/new", data={"client_id": "", "title": "בדיקה"})
    body = r.data.decode("utf-8")
    assert r.status_code == 200
    assert "שגיאה" in body or "יש להעלות" in body

def test_route_save_json():
    # Create a report first
    db.init_db()
    rid = db.create_report(None, "test", "meta", "", "", '{"slides":[]}', "{}")
    r = client.post(f"/reports/{rid}/save",
                    data=json.dumps({"slides": [{"type": "cover", "title": "כריכה"}]}),
                    content_type="application/json")
    assert r.status_code == 200
    assert json.loads(r.data)["ok"] is True

def test_route_embed_invalid_token():
    r = client.get("/embed/invalidtoken123")
    assert r.status_code == 404

check("GET / → 200 + תוכן", test_route_home)
check("GET /clients → 200", test_route_clients_list)
check("GET /clients/new → 200 + שדות צבע/לוגו", test_route_clients_new_get)
check("GET /reports → 200", test_route_reports_list)
check("GET /reports/new → 200 + שדות meta_file + google_file", test_route_reports_new_get)
check("GET /reports/999999/edit → 404", test_route_404)
check("POST /reports/new ללא קבצים → הודעת שגיאה", test_route_reports_new_no_file)
check("POST /reports/<id>/save JSON → ok:true", test_route_save_json)
check("GET /embed/invalid_token → 404", test_route_embed_invalid_token)


# ══════════════════════════════════════════════════════════════════════════════
# 7. FILE UPLOAD END-TO-END (without Claude API call)
# ══════════════════════════════════════════════════════════════════════════════
print("\n🔄 7. העלאת קבצים end-to-end (ללא Claude)")
from unittest.mock import patch

MOCK_SLIDES = [
    {"type": "cover", "title": "סיכום פברואר 2026", "subtitle": "חודש טוב",
     "image_url": None, "kpis": [{"value": "₪8,014", "label": "השקעה"}]},
    {"type": "kpi_slide", "title": "נתוני Meta", "subtitle": "מצוין",
     "image_url": None, "kpis": [{"value": "₪8,014", "label": "השקעה"},
                                   {"value": "27", "label": "רכישות"},
                                   {"value": "4.58", "label": "ROAS"},
                                   {"value": "₪297", "label": "CPA"}], "note": "הערה"}
]

def test_upload_meta_only():
    data = make_meta_csv()
    with patch("analysis.generate_blocks_with_claude", return_value=MOCK_SLIDES):
        r = client.post("/reports/new", data={
            "meta_file": (io.BytesIO(data), "meta_report.csv"),
            "title": "דוח Meta בדיקה",
            "client_id": "",
        }, content_type="multipart/form-data", follow_redirects=False)
    assert r.status_code == 302, f"expected redirect, got {r.status_code}: {r.data[:200]}"

def test_upload_utf16_meta():
    raw = b"\xff\xfe" + make_meta_csv("utf-8").decode().encode("utf-16-le")
    with patch("analysis.generate_blocks_with_claude", return_value=MOCK_SLIDES):
        r = client.post("/reports/new", data={
            "meta_file": (io.BytesIO(raw), "meta_utf16.csv"),
            "title": "דוח UTF-16",
            "client_id": "",
        }, content_type="multipart/form-data", follow_redirects=False)
    assert r.status_code == 302, f"expected redirect, got {r.status_code}: {r.data[:300]}"

def test_upload_google_only():
    data = make_google_csv()
    with patch("analysis.generate_blocks_with_claude", return_value=MOCK_SLIDES):
        r = client.post("/reports/new", data={
            "google_file": (io.BytesIO(data), "google_report.csv"),
            "title": "דוח Google בדיקה",
            "client_id": "",
        }, content_type="multipart/form-data", follow_redirects=False)
    assert r.status_code == 302, f"expected redirect, got {r.status_code}: {r.data[:200]}"

def test_upload_combined():
    meta_data   = make_meta_csv()
    google_data = make_google_csv()
    with patch("analysis.generate_combined_slides", return_value=MOCK_SLIDES):
        r = client.post("/reports/new", data={
            "meta_file":   (io.BytesIO(meta_data),   "meta.csv"),
            "google_file": (io.BytesIO(google_data), "google.csv"),
            "title": "דוח משולב בדיקה",
            "client_id": "",
        }, content_type="multipart/form-data", follow_redirects=False)
    assert r.status_code == 302, f"expected redirect, got {r.status_code}: {r.data[:300]}"

def test_upload_invalid_extension():
    r = client.post("/reports/new", data={
        "meta_file": (io.BytesIO(b"not a real file"), "report.pdf"),
        "client_id": "",
        "title": "pdf לא תקין",
    }, content_type="multipart/form-data")
    body = r.data.decode("utf-8")
    assert "שגיאה" in body or "יש להעלות" in body, "expected error message"

check("העלאת קובץ Meta בלבד → redirect לעורך", test_upload_meta_only)
check("העלאת קובץ Meta ב-UTF-16 (BOM) → redirect לעורך", test_upload_utf16_meta)
check("העלאת קובץ Google בלבד → redirect לעורך", test_upload_google_only)
check("העלאת Meta + Google יחד → redirect לעורך", test_upload_combined)
check("העלאת PDF (סיומת לא חוקית) → הודעת שגיאה", test_upload_invalid_extension)


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
total  = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed

print(f"\n{'═'*55}")
print(f"  תוצאות: {passed}/{total} עברו  {'✅' if failed == 0 else '❌'}")
if failed:
    print(f"\n  כשלים:")
    for name, ok, err in results:
        if not ok:
            print(f"    ✗ {name}")
            print(f"      {err}")
print(f"{'═'*55}\n")
sys.exit(0 if failed == 0 else 1)
