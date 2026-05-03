import json
import anthropic
import pandas as pd
import os

client = anthropic.Anthropic()

META_COLUMNS = {"campaign name", "amount spent", "results", "impressions", "reach", "link clicks", "ctr", "cpm", "roas"}
GOOGLE_COLUMNS = {"campaign", "cost", "conversions", "clicks", "impressions", "ctr", "avg. cpc"}
# Hebrew Google Ads column names (exported from Hebrew UI)
GOOGLE_COLUMNS_HE = {"קמפיין", "מחיר", "המרות", "קליקים", "חשיפות", "עלות ממוצעת לקליק", "שיעור קליקים"}

# Mapping Hebrew → normalised English column names
GOOGLE_HE_MAP = {
    "קמפיין":                "Campaign",
    "מחיר":                  "Cost",
    "המרות":                 "Conversions",
    "ערך המרה/מחיר":         "ROAS",
    "ערך המרה/ מחיר":        "ROAS",   # space after /
    "ערך / המרה":            "Conv. value / cost",
    "קליקים":                "Clicks",
    "חשיפות":                "Impressions",
    "שיעור קליקים (ctr)":    "CTR",
    "שיעור קליקים":          "CTR",
    "עלות ממוצעת לקליק":     "Avg. CPC",
    "סטטוס קמפיין":          "Campaign status",
    "סוג קמפיין":            "Campaign type",
    "ערך המרה":              "Conversion value",
    "מחיר / המרה":           "Cost / conv.",
    "עלות / המרה":           "Cost / conv.",
}

_GOOGLE_SUMMARY_PREFIXES = ("סך הכל", "total", "totals", '"סך"')


def _normalise_google_he_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Hebrew Google Ads column names to English equivalents."""
    rename = {}
    for col in df.columns:
        # Normalize: lowercase + collapse multiple spaces to one
        key = ' '.join(col.lower().split())
        if key in GOOGLE_HE_MAP:
            rename[col] = GOOGLE_HE_MAP[key]
    if rename:
        df = df.rename(columns=rename)
    return df


def detect_platform(df: pd.DataFrame) -> str:
    cols_lower = {c.lower().strip() for c in df.columns}
    meta_score   = len(cols_lower & META_COLUMNS)
    google_score = len(cols_lower & GOOGLE_COLUMNS)
    google_he    = len(cols_lower & {k.lower() for k in GOOGLE_COLUMNS_HE})
    google_score = max(google_score, google_he)
    return "meta" if meta_score >= google_score else "google_ads"


def parse_meta_file(df: pd.DataFrame) -> dict:
    # Normalized key map: collapse spaces, lowercase
    col_map = {' '.join(c.lower().split()): c for c in df.columns}

    def exact(*keys):
        for k in keys:
            if k in col_map:
                return col_map[k]
        return None

    def contains(key, exclude=None):
        for k, orig in col_map.items():
            if key in k:
                if exclude and any(ex in k for ex in ([exclude] if isinstance(exclude, str) else exclude)):
                    continue
                return orig
        return None

    spend_col       = exact("amount spent (ils)", "amount spent")
    # Use Purchases column (actual purchases), not Results (which can be reach/clicks)
    purchases_col   = exact("purchases", "website purchases")
    conv_value_col  = exact("purchases conversion value", "website purchases conversion value")
    roas_col        = exact("results roas", "purchase roas", "website purchase roas")
    impressions_col = exact("impressions")
    reach_col       = exact("reach")
    cpm_col         = contains("cpm")
    clicks_col      = exact("clicks (all)") or contains("outbound clicks") or contains("link clicks")
    campaign_col    = exact("campaign name")
    start_col       = exact("reporting starts")
    end_col         = exact("reporting ends")

    def safe_num(col):
        if col and col in df.columns:
            s = pd.to_numeric(df[col], errors='coerce')
            return float(s.sum()) if not s.isna().all() else 0.0
        return 0.0

    date_range = ""
    if start_col and end_col and start_col in df.columns and end_col in df.columns:
        start = df[start_col].dropna().min()
        end = df[end_col].dropna().max()
        if pd.notna(start) and pd.notna(end):
            date_range = f"{start} - {end}"

    campaigns = []
    if campaign_col and campaign_col in df.columns:
        for _, row in df.iterrows():
            name = str(row.get(campaign_col, "")).strip()
            if not name or name.lower() in ("nan", "total", ""):
                continue

            def n(col, as_int=False):
                if not col:
                    return 0
                v = pd.to_numeric(row.get(col, 0), errors='coerce')
                v = 0 if pd.isna(v) else v
                return int(v) if as_int else float(v)

            spend = n(spend_col)
            purchases = n(purchases_col, True)
            conv_val = n(conv_value_col)
            roas = conv_val / spend if spend > 0 else n(roas_col)
            campaigns.append({
                "name": name,
                "spend": spend,
                "results": purchases,
                "impressions": n(impressions_col, True),
                "reach": n(reach_col, True),
                "cpm": n(cpm_col),
                "roas": roas,
                "conv_value": conv_val,
            })

    total_spend = safe_num(spend_col)
    total_purchases = int(safe_num(purchases_col))
    total_impressions = int(safe_num(impressions_col))
    total_conv_value = safe_num(conv_value_col)
    avg_roas = total_conv_value / total_spend if total_spend > 0 else 0.0
    avg_cpm = (total_spend / max(total_impressions, 1)) * 1000

    return {
        "platform": "meta",
        "date_range": date_range,
        "total_spend": total_spend,
        "total_results": total_purchases,
        "total_impressions": total_impressions,
        "total_reach": int(safe_num(reach_col)),
        "total_clicks": int(safe_num(clicks_col)),
        "avg_cpm": avg_cpm,
        "avg_roas": avg_roas,
        "total_conv_value": total_conv_value,
        "campaigns": campaigns,
    }


def parse_google_ads_file(df: pd.DataFrame) -> dict:
    df = _normalise_google_he_columns(df)
    # Normalized key map: collapse spaces, lowercase
    col_map = {' '.join(c.lower().split()): c for c in df.columns}

    def exact(*keys):
        for k in keys:
            if k in col_map:
                return col_map[k]
        return None

    # Exact matches after normalization — prevents "cost / conv." matching "cost"
    cost_col        = exact("cost")
    conv_col        = exact("conversions")
    clicks_col      = exact("clicks")
    impressions_col = exact("impressions")
    ctr_col         = exact("ctr")
    cpc_col         = exact("avg. cpc")
    roas_col        = exact("roas")
    conv_value_col  = exact("conversion value")
    campaign_col    = exact("campaign")

    def _to_num(series):
        """Convert series to numeric, stripping comma thousands-separators first."""
        if series.dtype == object:
            series = series.astype(str).str.replace(',', '', regex=False)
        return pd.to_numeric(series, errors='coerce')

    def safe_num(col):
        if col and col in df.columns:
            s = _to_num(df[col])
            return float(s.sum()) if not s.isna().all() else 0.0
        return 0.0

    def safe_mean(col):
        if col and col in df.columns:
            s = _to_num(df[col])
            return float(s.mean()) if not s.isna().all() else 0.0
        return 0.0

    campaigns = []
    if campaign_col and campaign_col in df.columns:
        for _, row in df.iterrows():
            name = str(row.get(campaign_col, "")).strip()
            if not name or name.lower() in ("nan", "total", "", "--"):
                continue
            # Skip Hebrew/English summary rows
            if any(name.startswith(p) for p in _GOOGLE_SUMMARY_PREFIXES):
                continue

            def g(col, as_int=False):
                if not col:
                    return 0
                raw = str(row.get(col, 0)).replace(',', '')
                v = pd.to_numeric(raw, errors='coerce')
                v = 0 if pd.isna(v) else v
                return int(v) if as_int else float(v)

            spend = g(cost_col)
            conv_val = g(conv_value_col)
            roas = conv_val / spend if spend > 0 else g(roas_col)
            campaigns.append({
                "name": name,
                "spend": spend,
                "conversions": g(conv_col),
                "clicks": g(clicks_col, True),
                "impressions": g(impressions_col, True),
                "ctr": g(ctr_col),
                "cpc": g(cpc_col),
                "roas": roas,
                "conv_value": conv_val,
            })

    # Compute totals from filtered campaigns (excludes summary/total rows)
    total_spend       = sum(c['spend'] for c in campaigns)
    total_impressions = sum(c['impressions'] for c in campaigns)
    total_conv_value  = sum(c['conv_value'] for c in campaigns)
    total_conversions = sum(c['conversions'] for c in campaigns)
    total_clicks      = sum(c['clicks'] for c in campaigns)
    avg_roas = total_conv_value / total_spend if total_spend > 0 else 0.0
    avg_cpm  = (total_spend / max(total_impressions, 1)) * 1000

    return {
        "platform": "google_ads",
        "date_range": "",
        "total_spend": total_spend,
        "total_conversions": total_conversions,
        "total_clicks": total_clicks,
        "total_impressions": total_impressions,
        "avg_ctr": safe_mean(ctr_col),
        "avg_cpc": safe_mean(cpc_col),
        "avg_roas": avg_roas,
        "avg_cpm": avg_cpm,
        "total_conv_value": total_conv_value,
        "campaigns": campaigns,
    }


def _decode_raw(raw: bytes) -> str:
    """Decode raw bytes to string, handling all common BOMs and encodings."""
    if raw[:4] in (b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff'):
        text = raw.decode('utf-32')
    elif raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = raw.decode('utf-16')
    elif raw[:3] == b'\xef\xbb\xbf':
        text = raw.decode('utf-8-sig')
    else:
        for enc in ("utf-8", "cp1255", "iso-8859-8", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        raise ValueError("לא ניתן לזהות את קידוד הקובץ")
    # Strip any leftover BOM character from the string
    return text.lstrip('﻿')


def _find_header_row(lines: list[str]) -> int:
    """Find the first line that looks like a real CSV/TSV header."""
    HEADER_KEYWORDS = {
        # English
        "campaign name", "amount spent", "reporting starts", "impressions",
        "campaign", "cost", "conversions", "clicks", "date", "ctr",
        # Hebrew (Google Ads export)
        "קמפיין", "מחיר", "קליקים", "חשיפות", "המרות", "עלות", "סטטוס קמפיין",
    }
    for i, line in enumerate(lines[:20]):
        line_lower = line.lower()
        has_sep = '\t' in line or ',' in line
        if has_sep and any(kw in line_lower for kw in HEADER_KEYWORDS):
            return i
    return 0


def load_file(filepath: str) -> pd.DataFrame:
    import io as _io
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".xlsx", ".xls"):
        # Excel files may also have leading empty/metadata rows — skip them
        df = pd.read_excel(filepath, header=None)
        # Find the row that contains recognisable column names
        HEADER_KEYWORDS = {"campaign name", "amount spent", "reporting starts",
                           "impressions", "campaign", "cost", "conversions", "clicks"}
        header_row = 0
        for i, row in df.iterrows():
            row_str = " ".join(str(v).lower() for v in row if pd.notna(v))
            if any(kw in row_str for kw in HEADER_KEYWORDS):
                header_row = i
                break
        df = pd.read_excel(filepath, header=header_row)
        return df

    with open(filepath, "rb") as f:
        raw = f.read()

    text = _decode_raw(raw)

    # Normalise line endings
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    # Skip metadata rows that appear before the real CSV header
    start = _find_header_row(lines)
    clean_text = '\n'.join(lines[start:])

    # Auto-detect delimiter: tab or comma
    first_data = clean_text.split('\n')[0] if clean_text else ''
    sep = '\t' if first_data.count('\t') > first_data.count(',') else ','

    try:
        return pd.read_csv(_io.StringIO(clean_text), sep=sep)
    except Exception:
        return pd.read_csv(_io.StringIO(clean_text), sep=sep, on_bad_lines='skip')


def analyze_report(filepath: str, platform_override: str = None) -> tuple[dict, list]:
    df = load_file(filepath)
    platform = platform_override or detect_platform(df)
    metrics = parse_meta_file(df) if platform == "meta" else parse_google_ads_file(df)
    slides = generate_blocks_with_claude(metrics)
    return metrics, slides


def analyze_combined_report(meta_path: str = None, google_path: str = None) -> tuple[dict, list]:
    """Analyze one or two files and generate a unified report."""
    meta_metrics = None
    google_metrics = None

    if meta_path:
        df = load_file(meta_path)
        meta_metrics = parse_meta_file(df)

    if google_path:
        df = load_file(google_path)
        google_metrics = parse_google_ads_file(df)

    # If only one file was provided, fall back to single-platform flow
    if meta_metrics and not google_metrics:
        slides = generate_blocks_with_claude(meta_metrics)
        combined = meta_metrics
    elif google_metrics and not meta_metrics:
        slides = generate_blocks_with_claude(google_metrics)
        combined = google_metrics
    else:
        slides = generate_combined_slides(meta_metrics, google_metrics)
        combined = {"platform": "combined", "meta": meta_metrics, "google": google_metrics}

    return combined, slides


def _parse_claude_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def generate_blocks_with_claude(metrics: dict) -> list:
    platform = metrics.get("platform", "meta")
    is_meta = platform == "meta"

    active_campaigns = [c for c in metrics.get('campaigns', []) if c.get('spend', 0) > 0]

    total_conv_value = metrics.get('total_conv_value', 0)
    avg_cpm = metrics.get('avg_cpm', 0)

    if is_meta:
        date_range = metrics.get('date_range', '')
        period_label = date_range if date_range else 'תקופה נוכחית'
        purchases = metrics['total_results']
        metrics_text = f"""
פלטפורמה: Meta (Facebook/Instagram)
תקופה: {period_label}
הוצאה חודשית: ₪{metrics['total_spend']:,.0f}
כמות רכישות: {purchases:,}
ROAS: {metrics['avg_roas']:.2f}
ערך המרה (סך): ₪{total_conv_value:,.0f}
מספר חשיפות: {metrics['total_impressions']:,}
עלות ל-1000 חשיפות (CPM): ₪{avg_cpm:.2f}

קמפיינים פעילים:
""" + "\n".join(
            f"- {c['name']}: הוצאה ₪{c['spend']:,.0f}, רכישות {c['results']}, ROAS {c['roas']:.2f}, ערך המרה ₪{c.get('conv_value',0):,.0f}"
            for c in active_campaigns
        )
    else:
        period_label = metrics.get('date_range', 'תקופה נוכחית')
        purchases = metrics.get('total_conversions', 0)
        metrics_text = f"""
פלטפורמה: Google Ads
תקופה: {period_label}
הוצאה חודשית: ₪{metrics['total_spend']:,.0f}
כמות רכישות (המרות): {purchases:,.0f}
ROAS: {metrics['avg_roas']:.2f}
ערך המרה (סך): ₪{total_conv_value:,.0f}
מספר חשיפות: {metrics['total_impressions']:,}
עלות ל-1000 חשיפות (CPM): ₪{avg_cpm:.2f}

קמפיינים פעילים:
""" + "\n".join(
            f"- {c['name']}: הוצאה ₪{c['spend']:,.0f}, המרות {c.get('conversions',0):.0f}, ROAS {c['roas']:.2f}, ערך המרה ₪{c.get('conv_value',0):,.0f}"
            for c in active_campaigns
        )

    top3 = sorted(active_campaigns, key=lambda x: x.get('roas', 0), reverse=True)[:3]
    purchases_label = 'רכישות מיוחסות' if is_meta else 'המרות'
    platform_label = 'מטא' if is_meta else 'גוגל'
    purchases_val = metrics['total_results'] if is_meta else metrics.get('total_conversions', 0)

    prompt = f"""אתה אנליסט שיווק דיגיטלי בכיר. צור דוח מקצועי בעברית ללקוח בפורמט שקופיות (slides).

{metrics_text}

החזר JSON תקין בלבד (ללא כל טקסט אחר) במבנה הבא:
{{
  "slides": [
    {{
      "type": "cover",
      "title": "סיכום פעילות",
      "subtitle": "משפט אחד תמציתי על החודש – מה היה הטון הכללי (חיובי/מאתגר), מה בלט",
      "image_url": null,
      "kpis": [
        {{"value": "₪{metrics['total_spend']:,.0f}", "label": "הוצאה חודשית"}},
        {{"value": "{purchases_val:,.0f}", "label": "{purchases_label}"}},
        {{"value": "{metrics['avg_roas']:.2f}", "label": "ROAS"}}
      ]
    }},
    {{
      "type": "kpi_slide",
      "title": "נתוני פרסום ומגמות {platform_label}",
      "subtitle": "משפט הסבר קצר על מה המספרים מראים ומה המשמעות לעסק",
      "image_url": null,
      "kpis": [
        {{"value": "₪{metrics['total_spend']:,.0f}", "label": "הוצאה חודשית"}},
        {{"value": "{purchases_val:,.0f}", "label": "{purchases_label}"}},
        {{"value": "{metrics['avg_roas']:.2f}", "label": "ROAS"}},
        {{"value": "₪{total_conv_value:,.0f}", "label": "ערך המרה"}},
        {{"value": "{metrics['total_impressions']:,}", "label": "חשיפות"}},
        {{"value": "₪{avg_cpm:.2f}", "label": "עלות ל-1000 חשיפות"}}
      ],
      "note": "הערה ללקוח: {'מערכת מטא מייחסת לעצמה יותר רכישות גבוה יותר עקב חלון ייחוס / צפיות במודעות, אך ה-ROAS המערכתי משמש כמדד לאופטימיזציה פנימית של הקמפיינים.' if is_meta else 'נתוני גוגל מבוססים על המרות שנמדדו ישירות. מומלץ לאמת מול נתוני האנליטיקס.'}"
    }},
    {{
      "type": "recommendations_slide",
      "title": "המלצות לתקופה הבאה",
      "image_url": null,
      "items": [
        "המלצה 1 – ספציפית ומבוססת על הנתונים (תקציב, קמפיין ספציפי)",
        "המלצה 2 – ספציפית ומבוססת על הנתונים",
        "המלצה 3 – ספציפית ומבוססת על הנתונים"
      ]
    }}
  ]
}}

חשוב:
- כל הטקסטים בעברית
- ערכים מספריים מדויקים כולל פסיקים (₪12,450)
- המלצות ספציפיות מאוד – מה לשנות, באיזה קמפיין, כמה תקציב
- אל תוסיף שום טקסט מחוץ ל-JSON
- image_url תמיד null (יועלה ידנית)"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    data = _parse_claude_json(response.content[0].text)
    return data.get("slides", [])


def generate_combined_slides(meta: dict, google: dict) -> list:
    """Generate a unified report from Meta + Google Ads metrics."""

    meta_campaigns = [c for c in meta.get('campaigns', []) if c.get('spend', 0) > 0]
    google_campaigns = [c for c in google.get('campaigns', []) if c.get('spend', 0) > 0]
    total_spend = meta['total_spend'] + google['total_spend']
    date_range = meta.get('date_range', '') or google.get('date_range', '')

    top_meta = sorted(meta_campaigns, key=lambda x: x.get('roas', 0), reverse=True)[:2]
    top_google = sorted(google_campaigns, key=lambda x: x.get('roas', 0), reverse=True)[:2]

    meta_conv_value = meta.get('total_conv_value', 0)
    google_conv_value = google.get('total_conv_value', 0)
    meta_cpm = meta.get('avg_cpm', 0)
    google_cpm = google.get('avg_cpm', 0)

    prompt = f"""אתה אנליסט שיווק דיגיטלי בכיר. צור דוח מקצועי בעברית המשלב נתוני Meta ו-Google Ads.

=== נתוני META (Facebook/Instagram) ===
תקופה: {meta.get('date_range', 'לא ידוע')}
הוצאה חודשית: ₪{meta['total_spend']:,.0f}
כמות רכישות: {meta['total_results']:,}
ROAS: {meta['avg_roas']:.2f}
ערך המרה (סך): ₪{meta_conv_value:,.0f}
מספר חשיפות: {meta['total_impressions']:,}
עלות ל-1000 חשיפות: ₪{meta_cpm:.2f}

קמפיינים פעילים ב-Meta:
{chr(10).join(f"- {c['name']}: ₪{c['spend']:,.0f} | {c['results']} רכישות | ROAS {c['roas']:.2f} | ערך המרה ₪{c.get('conv_value',0):,.0f}" for c in meta_campaigns)}

=== נתוני GOOGLE ADS ===
הוצאה חודשית: ₪{google['total_spend']:,.0f}
כמות רכישות (המרות): {google.get('total_conversions',0):,.0f}
ROAS: {google['avg_roas']:.2f}
ערך המרה (סך): ₪{google_conv_value:,.0f}
מספר חשיפות: {google['total_impressions']:,}
עלות ל-1000 חשיפות: ₪{google_cpm:.2f}

קמפיינים פעילים ב-Google:
{chr(10).join(f"- {c['name']}: ₪{c['spend']:,.0f} | {c.get('conversions',0):.0f} המרות | ROAS {c['roas']:.2f} | ערך המרה ₪{c.get('conv_value',0):,.0f}" for c in google_campaigns)}

=== סיכום משולב ===
סך הוצאה כולל: ₪{total_spend:,.0f}

החזר JSON תקין בלבד (ללא שום טקסט נוסף):
{{
  "slides": [
    {{
      "type": "cover",
      "title": "סיכום פעילות",
      "subtitle": "משפט תמציתי אחד על ביצועי התקופה בשתי הפלטפורמות",
      "image_url": null,
      "kpis": [
        {{"value": "₪{total_spend:,.0f}", "label": "הוצאה חודשית כוללת"}},
        {{"value": "₪{meta['total_spend']:,.0f}", "label": "הוצאה Meta"}},
        {{"value": "₪{google['total_spend']:,.0f}", "label": "הוצאה Google"}}
      ]
    }},
    {{
      "type": "kpi_slide",
      "title": "נתוני Meta – Facebook / Instagram",
      "subtitle": "משפט הסבר על מה המספרים מראים ומה המשמעות",
      "image_url": null,
      "kpis": [
        {{"value": "₪{meta['total_spend']:,.0f}", "label": "הוצאה חודשית"}},
        {{"value": "{meta['total_results']:,}", "label": "כמות רכישות"}},
        {{"value": "{meta['avg_roas']:.2f}", "label": "ROAS"}},
        {{"value": "₪{meta_conv_value:,.0f}", "label": "ערך המרה"}},
        {{"value": "{meta['total_impressions']:,}", "label": "חשיפות"}},
        {{"value": "₪{meta_cpm:.2f}", "label": "עלות ל-1000 חשיפות"}}
      ],
      "note": "הערה ללקוח על אופן הייחוס של מטא ומה לקחת בחשבון"
    }},
    {{
      "type": "kpi_slide",
      "title": "נתוני Google Ads",
      "subtitle": "משפט הסבר על ביצועי גוגל והשוואה למטא",
      "image_url": null,
      "kpis": [
        {{"value": "₪{google['total_spend']:,.0f}", "label": "הוצאה חודשית"}},
        {{"value": "{google.get('total_conversions',0):,.0f}", "label": "כמות רכישות"}},
        {{"value": "{google['avg_roas']:.2f}", "label": "ROAS"}},
        {{"value": "₪{google_conv_value:,.0f}", "label": "ערך המרה"}},
        {{"value": "{google['total_impressions']:,}", "label": "חשיפות"}},
        {{"value": "₪{google_cpm:.2f}", "label": "עלות ל-1000 חשיפות"}}
      ],
      "note": "הערה ללקוח על ביצועי גוגל ומה מייחד את הערוץ הזה"
    }},
    {{
      "type": "recommendations_slide",
      "title": "המלצות לתקופה הבאה",
      "image_url": null,
      "items": [
        "המלצה 1 ספציפית ומבוססת נתונים (Meta)",
        "המלצה 2 ספציפית (Google Ads)",
        "המלצה 3 – חלוקת תקציב בין הפלטפורמות",
        "המלצה 4 – אופטימיזציה כוללת"
      ]
    }}
  ]
}}

חשוב: כל הטקסטים בעברית, ערכים מדויקים, המלצות ספציפיות מאוד, image_url תמיד null."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=5000,
        messages=[{"role": "user", "content": prompt}]
    )

    data = _parse_claude_json(response.content[0].text)
    return data.get("slides", [])
