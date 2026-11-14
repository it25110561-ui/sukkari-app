import base64
import csv
import io
import json
import os
import random
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, render_template, request, session

# ==============================================================================
# 1. إعدادات التطبيق والمتغيرات البيئية
# ==============================================================================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sukkari_smart_secret_key_2026")

DB_PATH = os.environ.get("DB_PATH", "sukkari.db")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
SMS_WEBHOOK_URL = os.environ.get("SMS_WEBHOOK_URL", "").strip()

COUNTRY_DIALECT_MAP = {
    "+964": {"country": "العراق", "dialect": "iraqi", "lang": "ar", "label": "اللهجة العراقية"},
    "+966": {"country": "السعودية", "dialect": "gulf", "lang": "ar", "label": "اللهجة الخليجية (السعودية)"},
    "+971": {"country": "الإمارات", "dialect": "gulf", "lang": "ar", "label": "اللهجة الخليجية (الإمارات)"},
    "+965": {"country": "الكويت", "dialect": "gulf", "lang": "ar", "label": "اللهجة الخليجية (الكويت)"},
    "+974": {"country": "قطر", "dialect": "gulf", "lang": "ar", "label": "اللهجة الخليجية (قطر)"},
    "+973": {"country": "البحرين", "dialect": "gulf", "lang": "ar", "label": "اللهجة الخليجية (البحرين)"},
    "+968": {"country": "عُمان", "dialect": "gulf", "lang": "ar", "label": "اللهجة الخليجية (عُمان)"},
    "+20":  {"country": "مصر", "dialect": "egyptian", "lang": "ar", "label": "اللهجة المصرية"},
    "+962": {"country": "الأردن", "dialect": "levantine", "lang": "ar", "label": "اللهجة الشامية (الأردن)"},
    "+961": {"country": "لبنان", "dialect": "levantine", "lang": "ar", "label": "اللهجة الشامية (لبنان)"},
    "+963": {"country": "سوريا", "dialect": "levantine", "lang": "ar", "label": "اللهجة الشامية (سوريا)"},
    "+970": {"country": "فلسطين", "dialect": "levantine", "lang": "ar", "label": "اللهجة الشامية (فلسطين)"},
    "+212": {"country": "المغرب", "dialect": "msa", "lang": "ar", "label": "العربية الفصحى المبسطة"},
    "+213": {"country": "الجزائر", "dialect": "msa", "lang": "ar", "label": "العربية الفصحى المبسطة"},
    "+216": {"country": "تونس", "dialect": "msa", "lang": "ar", "label": "العربية الفصحى المبسطة"},
    "+1":   {"country": "أمريكا / كندا", "dialect": "english", "lang": "en", "label": "English (International)"},
    "+44":  {"country": "بريطانيا", "dialect": "english", "lang": "en", "label": "English (UK)"},
    "+49":  {"country": "ألمانيا", "dialect": "english", "lang": "en", "label": "English (International)"},
    "+90":  {"country": "تركيا", "dialect": "english", "lang": "en", "label": "English (International)"},
}


# ==============================================================================
# 2. دوال المساعدة العامة وتنسيق البيانات
# ==============================================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def calc_bmi(weight, height):
    try:
        w = float(weight)
        h_m = float(height) / 100.0
        if w > 0 and h_m > 0:
            return round(w / (h_m * h_m), 1)
    except Exception:
        pass
    return 0


def normalize_phone(raw_phone, country_code="+964"):
    cc = str(country_code or "+964").strip()
    if not cc.startswith("+"):
        cc = "+" + cc.lstrip("0")

    digits = "".join(ch for ch in str(raw_phone or "") if ch.isdigit())
    cc_digits = cc.lstrip("+")

    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(cc_digits):
        local = digits[len(cc_digits):]
    else:
        local = digits.lstrip("0")

    return f"+{cc_digits}{local}", cc


def detect_country_info(country_code="+964", full_phone=""):
    cc = str(country_code or "").strip()
    if not cc and full_phone:
        for prefix in sorted(COUNTRY_DIALECT_MAP.keys(), key=len, reverse=True):
            if str(full_phone).startswith(prefix):
                cc = prefix
                break

    info = COUNTRY_DIALECT_MAP.get(cc)
    if info:
        return {
            "countryCode": cc,
            "countryName": info["country"],
            "dialect": info["dialect"],
            "lang": info["lang"],
            "dialectLabel": info["label"],
        }

    return {
        "countryCode": cc or "+964",
        "countryName": "دولي",
        "dialect": "english",
        "lang": "en",
        "dialectLabel": "English (International)",
    }


def get_ai_dialect_instruction(dialect):
    mapping = {
        "iraqi": "تحدث باللهجة العراقية الدارجة المهذبة والواضحة والمطمئنة (مثل: عيني، شلونك، هسه، زين، ماكو شي، تكدر).",
        "gulf": "تحدث باللهجة الخليجية المهذبة والواضحة والمطمئنة (مثل: يا هلا، شلونك، الحين، طيب، ما عليك شر).",
        "egyptian": "تحدث باللهجة المصرية البسيطة والودودة والمطمئنة (مثل: أهلاً بيك، إزيك، دلوقتي، كويس، ألف سلامة).",
        "levantine": "تحدث باللهجة الشامية اللطيفة والمطمئنة (مثل: أهلاً فيك، كيفك، هلق، منيح، سلامتك).",
        "msa": "تحدث باللغة العربية الفصحى المبسطة والواضحة والمطمئنة.",
        "english": "Respond in clear, warm, empathetic, easy-to-understand English suitable for a diabetes patient.",
    }
    return mapping.get(dialect, mapping["iraqi"])


def serialize_user(row):
    if not row:
        return None

    u = dict(row)
    u.pop("password", None)
    u["bmi"] = calc_bmi(u.get("weight", 0), u.get("height", 0))

    c_info = detect_country_info(u.get("countryCode", "+964"), u.get("phone", ""))
    u["dialectLabel"] = c_info["dialectLabel"]
    u["lang"] = c_info["lang"]

    json_fields = [
        ("periodicChecksJson", {}),
        ("travelChecklistJson", {}),
        ("preVisitChecklistJson", {}),
        ("walkingPlanJson", {"week": 1, "completedDays": 0}),
    ]
    for json_field, default_val in json_fields:
        clean_key = json_field.replace("Json", "")
        try:
            u[clean_key] = json.loads(u.get(json_field) or "{}")
        except Exception:
            u[clean_key] = default_val

    return u


def get_uploaded_image_data_url(file_storage):
    img_bytes = file_storage.read()
    if not img_bytes:
        return None
    mime_type = str(getattr(file_storage, "mimetype", "") or "image/jpeg")
    if not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64_img}"


# ==============================================================================
# 3. إدارة قاعدة البيانات والترقية التلقائية (Migrations)
# ==============================================================================

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'patient',
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            countryCode TEXT DEFAULT '+964',
            countryName TEXT DEFAULT 'العراق',
            dialect TEXT DEFAULT 'iraqi',
            isVerified INTEGER DEFAULT 1,
            age INTEGER DEFAULT 40,
            gender TEXT DEFAULT 'ذكر',
            weight REAL DEFAULT 75,
            height REAL DEFAULT 170,
            blood TEXT DEFAULT 'O+',
            diabetesType TEXT DEFAULT 'النوع الثاني',
            emergencyPhone TEXT DEFAULT '07700000000',
            doctorNotes TEXT DEFAULT '',
            targetGlucose INTEGER DEFAULT 120,
            correctionFactor INTEGER DEFAULT 40,
            carbRatio INTEGER DEFAULT 15,
            nextVisitDate TEXT DEFAULT '',
            lastFootCheck TEXT DEFAULT '',
            lastOralSkinCheck TEXT DEFAULT '',
            medReminderTime TEXT DEFAULT '',
            ramadanMode INTEGER DEFAULT 0,
            sickDayMode INTEGER DEFAULT 0,
            seniorMode INTEGER DEFAULT 0,
            stripsCount INTEGER DEFAULT 50,
            insulinPensCount INTEGER DEFAULT 5,
            lastInjectionSite TEXT DEFAULT '',
            periodicChecksJson TEXT DEFAULT '{}',
            travelChecklistJson TEXT DEFAULT '{}',
            preVisitChecklistJson TEXT DEFAULT '{}',
            walkingPlanJson TEXT DEFAULT '{"week":1,"completedDays":0}'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS otp_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            code TEXT NOT NULL,
            countryCode TEXT DEFAULT '+964',
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            val INTEGER NOT NULL,
            timing TEXT NOT NULL,
            meds TEXT DEFAULT '-',
            bp TEXT DEFAULT '',
            mood TEXT DEFAULT 'عادي',
            mealName TEXT DEFAULT '',
            ketones TEXT DEFAULT '',
            date TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_trackers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            track_date TEXT NOT NULL,
            water_cups INTEGER DEFAULT 0,
            walk_mins INTEGER DEFAULT 0,
            med_morning INTEGER DEFAULT 0,
            med_evening INTEGER DEFAULT 0,
            side_effects TEXT DEFAULT '',
            UNIQUE(patient_id, track_date),
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS neuropathy_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            score INTEGER DEFAULT 0,
            risk_level TEXT DEFAULT 'منخفض',
            answers_json TEXT DEFAULT '{}',
            date TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS calibration_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            meter_val REAL NOT NULL,
            lab_val REAL NOT NULL,
            accuracy_pct REAL NOT NULL,
            status TEXT NOT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            text TEXT NOT NULL,
            time TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            time TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS lab_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            abnormal TEXT DEFAULT '',
            advice TEXT DEFAULT '',
            date TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    existing_user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
    user_migrations = [
        ("phone", "TEXT DEFAULT ''"),
        ("countryCode", "TEXT DEFAULT '+964'"),
        ("countryName", "TEXT DEFAULT 'العراق'"),
        ("dialect", "TEXT DEFAULT 'iraqi'"),
        ("isVerified", "INTEGER DEFAULT 1"),
        ("targetGlucose", "INTEGER DEFAULT 120"),
        ("correctionFactor", "INTEGER DEFAULT 40"),
        ("carbRatio", "INTEGER DEFAULT 15"),
        ("nextVisitDate", "TEXT DEFAULT ''"),
        ("lastFootCheck", "TEXT DEFAULT ''"),
        ("lastOralSkinCheck", "TEXT DEFAULT ''"),
        ("medReminderTime", "TEXT DEFAULT ''"),
        ("ramadanMode", "INTEGER DEFAULT 0"),
        ("sickDayMode", "INTEGER DEFAULT 0"),
        ("seniorMode", "INTEGER DEFAULT 0"),
        ("stripsCount", "INTEGER DEFAULT 50"),
        ("insulinPensCount", "INTEGER DEFAULT 5"),
        ("lastInjectionSite", "TEXT DEFAULT ''"),
        ("periodicChecksJson", "TEXT DEFAULT '{}'"),
        ("travelChecklistJson", "TEXT DEFAULT '{}'"),
        ("preVisitChecklistJson", "TEXT DEFAULT '{}'"),
        ("walkingPlanJson", 'TEXT DEFAULT \'{"week":1,"completedDays":0}\''),
    ]
    for col_name, col_def in user_migrations:
        if col_name not in existing_user_cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

    existing_log_cols = [r[1] for r in cur.execute("PRAGMA table_info(logs)").fetchall()]
    log_migrations = [
        ("bp", "TEXT DEFAULT ''"),
        ("mood", "TEXT DEFAULT 'عادي'"),
        ("mealName", "TEXT DEFAULT ''"),
        ("ketones", "TEXT DEFAULT ''"),
    ]
    for col_name, col_def in log_migrations:
        if col_name not in existing_log_cols:
            cur.execute(f"ALTER TABLE logs ADD COLUMN {col_name} {col_def}")

    count = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        cur.execute("""
            INSERT INTO users (
                username, password, role, name, phone, countryCode, countryName, dialect,
                age, gender, weight, height, blood, diabetesType, emergencyPhone, doctorNotes
            )
            VALUES (?, ?, 'doctor', ?, '+9647701234567', '+964', 'العراق', 'iraqi', 45, 'ذكر', 80, 178, 'A+', '-', '07701234567', '')
        """, ("doctor", "123", "د. خالد السامرائي"))

        cur.execute("""
            INSERT INTO users (
                username, password, role, name, phone, countryCode, countryName, dialect,
                age, gender, weight, height, blood, diabetesType, emergencyPhone, doctorNotes,
                targetGlucose, correctionFactor, carbRatio, nextVisitDate
            )
            VALUES (?, ?, 'patient', ?, '+9647701112233', '+964', 'العراق', 'iraqi', 42, 'ذكر', 75, 175, 'O+', 'النوع الثاني', '07701112233', 'الالتزام بالمشي نص ساعة يومياً وتقليل الخبز والتمن.', 120, 40, 15, ?)
        """, ("patient", "123", "أحمد علي", (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")))
        pat_id = cur.lastrowid

        now_dt = datetime.now()
        sample_logs = [
            (pat_id, 142, "ورا الأكل", "4 وحدات أنسولين", "125/80", "نوم زين", "تمن ومرك فاصوليا", "", now_dt.strftime("%Y-%m-%d %H:%M")),
            (pat_id, 128, "على الريك", "-", "120/78", "عادي", "", "", (now_dt - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")),
            (pat_id, 112, "عشوائي", "-", "118/76", "نوم زين", "", "", (now_dt - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")),
            (pat_id, 135, "على الريك", "-", "122/80", "سهر وتعب", "", "", (now_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")),
        ]
        cur.executemany(
            "INSERT INTO logs (patient_id, val, timing, meds, bp, mood, mealName, ketones, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sample_logs,
        )

        cur.execute("""
            INSERT INTO messages (patient_id, sender, text, time)
            VALUES (?, 'doctor', 'هلا بيك أخ أحمد، قياساتك هالأسبوع زينة، استمر على نفس العلاج وبلغني بأي طارئ.', ?)
        """, (pat_id, now_dt.strftime("%H:%M")))

    conn.commit()
    conn.close()


init_db()


# ==============================================================================
# 4. محرك الذكاء الاصطناعي المرتبط بملف المراجع
# ==============================================================================

def build_patient_medical_context(uid):
    if not uid:
        return "iraqi", get_ai_dialect_instruction("iraqi"), ""

    conn = get_db()
    u_row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not u_row:
        conn.close()
        return "iraqi", get_ai_dialect_instruction("iraqi"), ""

    u = dict(u_row)
    dialect = u.get("dialect") or "iraqi"
    dialect_inst = get_ai_dialect_instruction(dialect)

    logs = conn.execute(
        "SELECT val, timing, bp, mood, mealName, date FROM logs WHERE patient_id = ? ORDER BY id DESC LIMIT 6",
        (uid,),
    ).fetchall()
    latest_lab = conn.execute(
        "SELECT title, summary, abnormal, date FROM lab_reports WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()
    conn.close()

    logs_summary = "، ".join(
        f"{l['val']} ({l['timing']}{' - وجبة: ' + l['mealName'] if l['mealName'] else ''})"
        for l in logs
    ) or "لا توجد قياسات مسجلة بعد"

    modes = []
    if u.get("ramadanMode"):
        modes.append("وضع الصيام ورمضان مفعّل حالياً")
    if u.get("sickDayMode"):
        modes.append("وضع أيام المرض/الفلاونزا مفعّل حالياً")
    modes_str = " | ".join(modes) if modes else "الوضع الاعتيادي"

    lab_str = f"{latest_lab['title']} ({latest_lab['date']}): {latest_lab['abnormal']}" if latest_lab else "لا يوجد"

    context_block = (
        f"بيانات المراجع الشخصية والطبية (استخدمها لتخصيص إجابتك بدقة):\n"
        f"- الاسم: {u.get('name')} | العمر: {u.get('age')} سنة | الجنس: {u.get('gender')}\n"
        f"- الوزن: {u.get('weight')} كغم | الطول: {u.get('height')} سم | BMI: {calc_bmi(u.get('weight'), u.get('height'))}\n"
        f"- نوع السكري: {u.get('diabetesType')} | الدولة: {u.get('countryName')}\n"
        f"- السكر المستهدف: {u.get('targetGlucose', 120)} mg/dL | معامل الكارب: 1 وحدة لكل {u.get('carbRatio', 15)} غرام كارب | معامل التصحيح: {u.get('correctionFactor', 40)}\n"
        f"- الحالة الحالية: {modes_str}\n"
        f"- آخر قياسات السكر: {logs_summary}\n"
        f"- آخر تحليل مختبري: {lab_str}\n"
        f"- توجيهات الطبيب المشرف: {u.get('doctorNotes') or 'لا توجد ملاحظات خاصة'}"
    )
    return dialect, dialect_inst, context_block


def call_openrouter(messages, max_tokens=750, temperature=0.35):
    if not OPENROUTER_API_KEY:
        return None, "مفتاح الذكاء الاصطناعي (OPENROUTER_API_KEY) غير مضبوط في إعدادات السيرفر."

    models = ["google/gemini-2.0-flash-001", "google/gemini-2.5-flash", "openai/gpt-4o-mini"]
    last_error = "تعذر الاتصال بخدمة الذكاء الاصطناعي."

    for model in models:
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://sukkari-app.local",
                "X-Title": "Sukkari Smart Health",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                return content, None
        except urllib.error.HTTPError as e:
            last_error = f"خطأ من المزود ({e.code})"
        except Exception as e:
            last_error = str(e)

    return None, last_error


def extract_json_block(text):
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


# ==============================================================================
# 5. خوارزميات التحليل السريري المتقدمة
# ==============================================================================

def compute_patient_analytics(logs, user_dict, daily_rows):
    vals = [int(l["val"]) for l in logs if l.get("val") is not None]
    total = len(vals)

    if total > 0:
        in_range = sum(1 for v in vals if 70 <= v <= 180)
        low_count = sum(1 for v in vals if v < 70)
        high_count = sum(1 for v in vals if v > 180)
        tir = {
            "inRangePct": round((in_range / total) * 100),
            "lowPct": round((low_count / total) * 100),
            "highPct": round((high_count / total) * 100),
            "total": total,
        }
    else:
        tir = {"inRangePct": 0, "lowPct": 0, "highPct": 0, "total": 0}

    recent_vals = vals[:10]
    swing_diff = (max(recent_vals) - min(recent_vals)) if len(recent_vals) >= 2 else 0
    has_dangerous_swing = bool(
        swing_diff >= 130 or (any(v < 70 for v in recent_vals) and any(v > 220 for v in recent_vals))
    )

    fasting_vals = [
        int(l["val"]) for l in logs
        if "ريك" in str(l.get("timing", "")) or "صائم" in str(l.get("timing", ""))
    ]
    bedtime_vals = [
        int(l["val"]) for l in logs
        if "عشوائي" in str(l.get("timing", "")) or "نوم" in str(l.get("timing", ""))
    ]

    dawn_phenomenon = False
    dawn_msg = ""
    if fasting_vals and bedtime_vals:
        f_avg = sum(fasting_vals[:5]) / len(fasting_vals[:5])
        b_avg = sum(bedtime_vals[:5]) / len(bedtime_vals[:5])
        if f_avg - b_avg >= 25 and f_avg > 130:
            dawn_phenomenon = True
            dawn_msg = (
                f"متوسط الصائم ({round(f_avg)}) أعلى من قبل النوم ({round(b_avg)}) "
                f"بمقدار {round(f_avg - b_avg)} درجة — احتمالية ظاهرة الفجر."
            )

    target_g = int(user_dict.get("targetGlucose") or 120)
    last_3_fasting = fasting_vals[:3]
    basal_titration_alert = len(last_3_fasting) == 3 and all(v > target_g for v in last_3_fasting)

    time_groups = {}
    for l in logs:
        t = l.get("timing") or "عشوائي"
        time_groups.setdefault(t, []).append(int(l["val"]))
    time_of_day = [
        {"timing": k, "avg": round(sum(v) / len(v)), "count": len(v)}
        for k, v in time_groups.items()
    ]

    now_dt = datetime.now()
    this_week_vals = []
    last_week_vals = []
    for l in logs:
        try:
            dt = datetime.strptime(str(l["date"])[:16], "%Y-%m-%d %H:%M")
            days_diff = (now_dt - dt).days
            if 0 <= days_diff < 7:
                this_week_vals.append(int(l["val"]))
            elif 7 <= days_diff < 14:
                last_week_vals.append(int(l["val"]))
        except Exception:
            pass

    this_w_avg = round(sum(this_week_vals) / len(this_week_vals)) if this_week_vals else 0
    last_w_avg = round(sum(last_week_vals) / len(last_week_vals)) if last_week_vals else 0
    weekly_comparison = {
        "thisWeekAvg": this_w_avg,
        "lastWeekAvg": last_w_avg,
        "diff": (this_w_avg - last_w_avg) if (this_w_avg and last_w_avg) else 0,
        "thisWeekHypos": sum(1 for v in this_week_vals if v < 70),
        "lastWeekHypos": sum(1 for v in last_week_vals if v < 70),
    }

    safe_foods = []
    spike_foods = []
    for l in logs:
        m_name = str(l.get("mealName") or "").strip()
        if m_name:
            v = int(l["val"])
            if v <= 165 and m_name not in safe_foods:
                safe_foods.append(m_name)
            elif v > 180 and m_name not in spike_foods:
                spike_foods.append(m_name)

    unique_days = sorted({str(l["date"])[:10] for l in logs if l.get("date")}, reverse=True)
    streak = 0
    check_day = now_dt.date()
    for d_str in unique_days:
        try:
            d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            if d_obj == check_day or d_obj == check_day - timedelta(days=1):
                streak += 1
                check_day = d_obj - timedelta(days=1)
            else:
                break
        except Exception:
            break

    badges = []
    if streak >= 3:
        badges.append(f"🔥 التزام {streak} أيام متواصلة")
    if len(fasting_vals) >= 3:
        badges.append("🌅 بطل القياس الصباحي")
    if tir["inRangePct"] >= 70 and total >= 3:
        badges.append("🎯 نطاق آمن ممتاز (+70%)")

    days_since_last = 999
    if logs:
        try:
            last_dt = datetime.strptime(str(logs[0]["date"])[:16], "%Y-%m-%d %H:%M")
            days_since_last = max(0, (now_dt - last_dt).days)
        except Exception:
            days_since_last = 0

    adherence_days = [r for r in daily_rows if (r.get("med_morning") or r.get("med_evening"))]
    adherence_pct = round((len(adherence_days) / len(daily_rows)) * 100) if daily_rows else 100
    latest_side_effects = next((r["side_effects"] for r in daily_rows if r.get("side_effects")), "")

    f_avg_str = f"{round(sum(fasting_vals) / len(fasting_vals))}" if fasting_vals else "-"
    post_vals = [
        int(l["val"]) for l in logs
        if "أكل" in str(l.get("timing", "")) or "فطور" in str(l.get("timing", ""))
    ]
    p_avg_str = f"{round(sum(post_vals) / len(post_vals))}" if post_vals else "-"
    min_max_str = f"{min(vals)} / {max(vals)}" if vals else "-"

    return {
        "tir": tir,
        "swingDiff": swing_diff,
        "hasDangerousSwing": has_dangerous_swing,
        "dawnPhenomenon": dawn_phenomenon,
        "dawnMsg": dawn_msg,
        "basalTitrationAlert": basal_titration_alert,
        "timeOfDay": time_of_day,
        "weeklyComparison": weekly_comparison,
        "safeFoods": safe_foods[:6],
        "spikeFoods": spike_foods[:6],
        "streak": streak,
        "badges": badges,
        "daysSinceLastLog": days_since_last,
        "isInactive": bool(days_since_last > 7),
        "adherencePct": adherence_pct,
        "latestSideEffects": latest_side_effects,
        "fiveSecondSummary": {
            "fastingAvg": f_avg_str,
            "postMealAvg": p_avg_str,
            "minMax": min_max_str,
            "adherencePct": adherence_pct,
        },
    }


# ==============================================================================
# 6. مسارات الواجهة وتطبيق الويب التقدمي (PWA)
# ==============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/icon.svg")
def app_icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#10b981"/>
          <stop offset="100%" stop-color="#0f766e"/>
        </linearGradient>
      </defs>
      <rect width="512" height="512" rx="115" fill="url(#g)"/>
      <path d="M256 392s-124-78-124-174c0-46 36-82 80-82 26 0 44 14 44 14s18-14 44-14c44 0 80 36 80 82 0 96-124 174-124 174z" fill="#ffffff"/>
      <polyline points="170,235 215,235 235,195 262,275 285,225 342,225" fill="none" stroke="#059669" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>"""
    return Response(svg, mimetype="image/svg+xml")


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "سُكّري الذكي - متابعة السكري",
        "short_name": "سُكّري",
        "description": "منصة ذكية لمتابعة قياسات السكر والتواصل المباشر مع الطبيب",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#059669",
        "dir": "rtl",
        "lang": "ar",
        "icons": [
            {
                "src": "/icon.svg",
                "sizes": "192x192 512x512",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    })


@app.route("/sw.js")
def service_worker():
    js = """
    self.addEventListener('install', (e) => self.skipWaiting());
    self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));
    self.addEventListener('fetch', (e) => {
        if (e.request.method !== 'GET') return;
        e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    });
    """
    return Response(js, mimetype="application/javascript")


# ==============================================================================
# 7. مسارات المصادقة والتسجيل برقم الهاتف + رمز التحقق (OTP)
# ==============================================================================

@app.route("/api/me")
def api_me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"logged_in": False})
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    if not row:
        session.clear()
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "user": serialize_user(row)})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE (username = ? OR phone = ?) AND password = ?",
        (username, username, password),
    ).fetchone()

    if not row and any(ch.isdigit() for ch in username):
        norm_p, _ = normalize_phone(username, "+964")
        row = conn.execute(
            "SELECT * FROM users WHERE (username = ? OR phone = ?) AND password = ?",
            (norm_p, norm_p, password),
        ).fetchone()

    conn.close()

    if not row:
        return jsonify({"success": False, "message": "اليوزر / الرقم أو الرمز السري غير صحيح"})

    session["user_id"] = row["id"]
    session["role"] = row["role"]
    return jsonify({"success": True, "user": serialize_user(row)})


@app.route("/api/auth/send-otp", methods=["POST"])
def api_send_otp():
    data = request.get_json(silent=True) or {}
    raw_phone = str(data.get("phone", "")).strip()
    country_code = str(data.get("countryCode", "+964")).strip()

    if not raw_phone or len("".join(c for c in raw_phone if c.isdigit())) < 7:
        return jsonify({"success": False, "message": "يرجى إدخال رقم هاتف صحيح"})

    norm_phone, cc = normalize_phone(raw_phone, country_code)
    c_info = detect_country_info(cc, norm_phone)

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE phone = ? OR username = ?",
        (norm_phone, norm_phone),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"success": False, "message": "هذا الرقم مسجل مسبقاً! تكدر تسجل دخولك مباشرة."})

    otp_code = f"{random.randint(100000, 999999)}"
    now_dt = datetime.now()
    expires_dt = now_dt + timedelta(minutes=10)

    conn.execute("DELETE FROM otp_codes WHERE phone = ?", (norm_phone,))
    conn.execute(
        "INSERT INTO otp_codes (phone, code, countryCode, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (norm_phone, otp_code, cc, expires_dt.strftime("%Y-%m-%d %H:%M:%S"), now_dt.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()

    sms_sent_externally = False
    if SMS_WEBHOOK_URL:
        try:
            payload = json.dumps({
                "phone": norm_phone,
                "message": f"رمز التحقق الخاص بك في منصة سُكّري هو: {otp_code}",
            }).encode("utf-8")
            req = urllib.request.Request(
                SMS_WEBHOOK_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            sms_sent_externally = True
        except Exception:
            sms_sent_externally = False

    resp_data = {
        "success": True,
        "normalizedPhone": norm_phone,
        "countryInfo": c_info,
        "message": f"تم إرسال رمز التحقق إلى {norm_phone} ({c_info['countryName']} - {c_info['dialectLabel']})",
    }
    if not sms_sent_externally:
        resp_data["demo_otp"] = otp_code

    return jsonify(resp_data)


@app.route("/api/auth/verify-register", methods=["POST"])
def api_verify_register():
    data = request.get_json(silent=True) or {}
    raw_phone = str(data.get("phone", "")).strip()
    country_code = str(data.get("countryCode", "+964")).strip()
    otp_input = str(data.get("otp", "")).strip()
    name = str(data.get("name", "")).strip()
    password = str(data.get("password", "")).strip()

    if not name or not password or not raw_phone or not otp_input:
        return jsonify({"success": False, "message": "يرجى إكمال جميع الحقول ورمز التحقق"})

    norm_phone, cc = normalize_phone(raw_phone, country_code)
    c_info = detect_country_info(cc, norm_phone)

    conn = get_db()
    otp_row = conn.execute(
        "SELECT * FROM otp_codes WHERE phone = ? ORDER BY id DESC LIMIT 1",
        (norm_phone,),
    ).fetchone()

    if not otp_row or otp_row["code"] != otp_input:
        conn.close()
        return jsonify({"success": False, "message": "رمز التحقق غير صحيح، تأكد من الرقم وحاول مرة ثانية"})

    try:
        exp_dt = datetime.strptime(otp_row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() > exp_dt:
            conn.close()
            return jsonify({"success": False, "message": "انتهت صلاحية رمز التحقق، اطلب رمزاً جديداً"})
    except Exception:
        pass

    exists = conn.execute(
        "SELECT id FROM users WHERE phone = ? OR username = ?",
        (norm_phone, norm_phone),
    ).fetchone()
    if exists:
        conn.close()
        return jsonify({"success": False, "message": "هذا الرقم مسجل مسبقاً"})

    age = int(data.get("age") or 38)
    gender = str(data.get("gender") or "ذكر").strip()
    weight = float(data.get("weight") or 75)
    height = float(data.get("height") or 170)
    blood = str(data.get("blood") or "O+").strip()
    d_type = str(data.get("diabetesType") or "النوع الثاني").strip()
    emerg_phone = str(data.get("emergencyPhone") or norm_phone).strip()

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (
            username, password, role, name, phone, countryCode, countryName, dialect, isVerified,
            age, gender, weight, height, blood, diabetesType, emergencyPhone
        )
        VALUES (?, ?, 'patient', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
    """, (
        norm_phone, password, name, norm_phone, cc, c_info["countryName"], c_info["dialect"],
        age, gender, weight, height, blood, d_type, emerg_phone,
    ))
    new_uid = cur.lastrowid

    welcome_msg = (
        f"Welcome {name}! Your account is verified and set to English."
        if c_info["lang"] == "en"
        else f"أهلاً بك {name}! تم تفعيل حسابك بنجاح وضبط النظام على {c_info['dialectLabel']} حسب دولتك ({c_info['countryName']})."
    )
    cur.execute(
        "INSERT INTO messages (patient_id, sender, text, time) VALUES (?, 'doctor', ?, ?)",
        (new_uid, welcome_msg, datetime.now().strftime("%H:%M")),
    )
    cur.execute("DELETE FROM otp_codes WHERE phone = ?", (norm_phone,))
    conn.commit()

    user_row = conn.execute("SELECT * FROM users WHERE id = ?", (new_uid,)).fetchone()
    conn.close()

    session["user_id"] = new_uid
    session["role"] = "patient"
    return jsonify({"success": True, "user": serialize_user(user_row)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})


# ==============================================================================
# 8. مسارات ملف المريض والأدوات التفاعلية اليومية
# ==============================================================================

@app.route("/api/patient/update-profile", methods=["POST"])
def update_patient_profile():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "message": "غير مسجل الدخول"}), 401

    data = request.get_json(silent=True) or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False}), 404

    cur_u = dict(row)
    cc = str(data.get("countryCode") or cur_u.get("countryCode") or "+964").strip()
    c_info = detect_country_info(cc)
    dialect_override = str(data.get("dialect") or "").strip()
    final_dialect = dialect_override if dialect_override else c_info["dialect"]

    age = int(data.get("age") if data.get("age") is not None else cur_u.get("age", 40))
    weight = float(data.get("weight") if data.get("weight") is not None else cur_u.get("weight", 70))
    height = float(data.get("height") if data.get("height") is not None else cur_u.get("height", 170))
    phone = str(data.get("emergencyPhone") if data.get("emergencyPhone") is not None else cur_u.get("emergencyPhone", "")).strip()
    med_time = str(data.get("medReminderTime") if data.get("medReminderTime") is not None else cur_u.get("medReminderTime", "")).strip()

    ramadan_mode = int(bool(data["ramadanMode"])) if "ramadanMode" in data else int(cur_u.get("ramadanMode") or 0)
    sick_day_mode = int(bool(data["sickDayMode"])) if "sickDayMode" in data else int(cur_u.get("sickDayMode") or 0)
    senior_mode = int(bool(data["seniorMode"])) if "seniorMode" in data else int(cur_u.get("seniorMode") or 0)

    strips_count = int(data["stripsCount"]) if "stripsCount" in data else int(cur_u.get("stripsCount") or 50)
    pens_count = int(data["insulinPensCount"]) if "insulinPensCount" in data else int(cur_u.get("insulinPensCount") or 5)
    inj_site = str(data["lastInjectionSite"]) if "lastInjectionSite" in data else str(cur_u.get("lastInjectionSite") or "")
    last_foot = str(data["lastFootCheck"]) if "lastFootCheck" in data else str(cur_u.get("lastFootCheck") or "")
    last_oral = str(data["lastOralSkinCheck"]) if "lastOralSkinCheck" in data else str(cur_u.get("lastOralSkinCheck") or "")

    periodic_json = (
        json.dumps(data["periodicChecks"], ensure_ascii=False)
        if "periodicChecks" in data
        else cur_u.get("periodicChecksJson", "{}")
    )
    travel_json = (
        json.dumps(data["travelChecklist"], ensure_ascii=False)
        if "travelChecklist" in data
        else cur_u.get("travelChecklistJson", "{}")
    )
    previsit_json = (
        json.dumps(data["preVisitChecklist"], ensure_ascii=False)
        if "preVisitChecklist" in data
        else cur_u.get("preVisitChecklistJson", "{}")
    )
    walking_json = (
        json.dumps(data["walkingPlan"], ensure_ascii=False)
        if "walkingPlan" in data
        else cur_u.get("walkingPlanJson", '{"week":1,"completedDays":0}')
    )

    conn.execute("""
        UPDATE users SET
            age = ?, weight = ?, height = ?, emergencyPhone = ?, medReminderTime = ?,
            countryCode = ?, countryName = ?, dialect = ?,
            ramadanMode = ?, sickDayMode = ?, seniorMode = ?,
            stripsCount = ?, insulinPensCount = ?, lastInjectionSite = ?,
            lastFootCheck = ?, lastOralSkinCheck = ?,
            periodicChecksJson = ?, travelChecklistJson = ?, preVisitChecklistJson = ?, walkingPlanJson = ?
        WHERE id = ?
    """, (
        age, weight, height, phone, med_time,
        cc, c_info["countryName"], final_dialect,
        ramadan_mode, sick_day_mode, senior_mode,
        strips_count, pens_count, inj_site,
        last_foot, last_oral,
        periodic_json, travel_json, previsit_json, walking_json,
        uid,
    ))
    conn.commit()
    updated = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return jsonify({"success": True, "user": serialize_user(updated)})


@app.route("/api/patient/foot-check", methods=["POST"])
def record_foot_check():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False}), 401
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    conn.execute("UPDATE users SET lastFootCheck = ? WHERE id = ?", (today_str, uid))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return jsonify({"success": True, "lastFootCheck": today_str, "user": serialize_user(row)})


@app.route("/api/patient/daily-tracker", methods=["GET", "POST"])
def handle_daily_tracker():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False}), 401

    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM daily_trackers WHERE patient_id = ? AND track_date = ?",
        (uid, today_str),
    ).fetchone()

    if not row:
        conn.execute(
            "INSERT INTO daily_trackers (patient_id, track_date, water_cups, walk_mins, med_morning, med_evening, side_effects) VALUES (?, ?, 0, 0, 0, 0, '')",
            (uid, today_str),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM daily_trackers WHERE patient_id = ? AND track_date = ?",
            (uid, today_str),
        ).fetchone()

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        cur_d = dict(row)
        water = int(data.get("water_cups", cur_d["water_cups"]))
        walk = int(data.get("walk_mins", cur_d["walk_mins"]))
        med_m = int(bool(data["med_morning"])) if "med_morning" in data else cur_d["med_morning"]
        med_e = int(bool(data["med_evening"])) if "med_evening" in data else cur_d["med_evening"]
        side_eff = str(data.get("side_effects", cur_d["side_effects"])).strip()

        conn.execute("""
            UPDATE daily_trackers
            SET water_cups = ?, walk_mins = ?, med_morning = ?, med_evening = ?, side_effects = ?
            WHERE id = ?
        """, (water, walk, med_m, med_e, side_eff, cur_d["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM daily_trackers WHERE id = ?", (cur_d["id"],)).fetchone()

    conn.close()
    return jsonify({"success": True, "tracker": dict(row)})


@app.route("/api/patient/neuropathy-check", methods=["POST"])
def save_neuropathy_check():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False}), 401
    data = request.get_json(silent=True) or {}
    score = int(data.get("score") or 0)
    risk = (
        "مرتفع - يرجى مراجعة الطبيب"
        if score >= 3
        else ("متوسط - يحتاج متابعة" if score >= 1 else "طبيعي وسليم")
    )
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = get_db()
    conn.execute(
        "INSERT INTO neuropathy_checks (patient_id, score, risk_level, answers_json, date) VALUES (?, ?, ?, ?, ?)",
        (uid, score, risk, json.dumps(data.get("answers", {}), ensure_ascii=False), date_str),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "score": score, "risk_level": risk, "date": date_str})


@app.route("/api/patient/calibration-check", methods=["POST"])
def save_calibration_check():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False}), 401
    data = request.get_json(silent=True) or {}
    meter_val = float(data.get("meter_val") or 0)
    lab_val = float(data.get("lab_val") or 0)
    if meter_val <= 0 or lab_val <= 0:
        return jsonify({"success": False, "message": "أدخل قراءة الجهاز وقراءة المختبر بشكل صحيح"})

    diff_pct = abs(meter_val - lab_val) / lab_val * 100.0
    accuracy = round(max(0.0, 100.0 - diff_pct), 1)
    status = (
        "جهازك دقيق ومضبوط (ضمن معيار ±15%)"
        if diff_pct <= 15
        else "فرق ملحوظ! ينصح بفحص الأشرطة أو البطارية"
    )
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = get_db()
    conn.execute(
        "INSERT INTO calibration_logs (patient_id, meter_val, lab_val, accuracy_pct, status, date) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, meter_val, lab_val, accuracy, status, date_str),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "accuracy_pct": accuracy, "status": status})


# ==============================================================================
# 9. مسارات القياسات والتحليلات السريرية
# ==============================================================================

@app.route("/api/logs", methods=["GET", "POST"])
def handle_logs():
    uid = session.get("user_id")
    if not uid:
        return jsonify([]), 401

    conn = get_db()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        val = int(float(data.get("val") or 0))
        timing = str(data.get("timing", "عشوائي")).strip()
        meds = str(data.get("meds", "-")).strip() or "-"
        bp = str(data.get("bp", "")).strip()
        mood = str(data.get("mood", "عادي")).strip() or "عادي"
        meal_name = str(data.get("mealName", "")).strip()
        ketones = str(data.get("ketones", "")).strip()
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        conn.execute(
            "INSERT INTO logs (patient_id, val, timing, meds, bp, mood, mealName, ketones, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, val, timing, meds, bp, mood, meal_name, ketones, date_str),
        )
        conn.execute("UPDATE users SET stripsCount = MAX(0, COALESCE(stripsCount, 50) - 1) WHERE id = ?", (uid,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    rows = conn.execute(
        "SELECT id, val, timing, meds, bp, mood, mealName, ketones, date FROM logs WHERE patient_id = ? ORDER BY id DESC",
        (uid,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/patient/full-analytics")
def get_patient_full_analytics():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False}), 401

    conn = get_db()
    u_row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    logs = [
        dict(r) for r in conn.execute("SELECT * FROM logs WHERE patient_id = ? ORDER BY id DESC", (uid,)).fetchall()
    ]
    daily_rows = [
        dict(r) for r in conn.execute(
            "SELECT * FROM daily_trackers WHERE patient_id = ? ORDER BY track_date DESC LIMIT 14",
            (uid,),
        ).fetchall()
    ]
    labs = [
        dict(r) for r in conn.execute("SELECT * FROM lab_reports WHERE patient_id = ? ORDER BY id DESC", (uid,)).fetchall()
    ]
    neuro = conn.execute(
        "SELECT * FROM neuropathy_checks WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()
    calib = conn.execute(
        "SELECT * FROM calibration_logs WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()
    conn.close()

    user_dict = serialize_user(u_row)
    analytics = compute_patient_analytics(logs, user_dict, daily_rows)

    return jsonify({
        "success": True,
        "user": user_dict,
        "logs": logs,
        "analytics": analytics,
        "labReports": labs,
        "latestNeuropathy": dict(neuro) if neuro else None,
        "latestCalibration": dict(calib) if calib else None,
    })


# ==============================================================================
# 10. مسارات الذكاء الاصطناعي (المحادثة + تحليل الصور المتقدم)
# ==============================================================================

@app.route("/api/ai/history", methods=["GET", "DELETE"])
def ai_chat_history():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False}), 401

    conn = get_db()
    if request.method == "DELETE":
        conn.execute("DELETE FROM ai_messages WHERE patient_id = ?", (uid,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    rows = conn.execute(
        "SELECT id, role, content, time FROM ai_messages WHERE patient_id = ? ORDER BY id ASC LIMIT 40",
        (uid,),
    ).fetchall()
    conn.close()
    return jsonify({"success": True, "messages": [dict(r) for r in rows]})


@app.route("/api/ai/assistant", methods=["POST"])
def ai_assistant():
    uid = session.get("user_id")
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"success": False, "answer": "اكتب سؤالك حتى أجاوبك."})

    _, dialect_inst, patient_ctx = build_patient_medical_context(uid)
    system_msg = (
        f"أنت المساعد الطبي الذكي الخاص بمنصة (سُكّري)، متخصص بمرض السكري والتغذية العلاجية ومتابعة القياسات. "
        f"{dialect_inst}\n\n"
        f"{patient_ctx}\n\n"
        "قواعد الإجابة:\n"
        "1. اجعل إجابتك منظمة بنقاط قصيرة وواضحة ومباشرة بدون حشو.\n"
        "2. اربط نصيحتك بأرقام المراجع وقياسات سكره الأخيرة ومعامل الكارب الخاص به.\n"
        "3. إذا ذكر المراجع قياساً أقل من 70 أو أعلى من 300 أو أعراض خطيرة، نبهه فوراً لإجراءات الطوارئ ومراجعة طبيبه."
    )

    history_msgs = []
    if uid:
        conn = get_db()
        prev_rows = conn.execute(
            "SELECT role, content FROM ai_messages WHERE patient_id = ? ORDER BY id DESC LIMIT 8",
            (uid,),
        ).fetchall()
        conn.close()
        for r in reversed(prev_rows):
            history_msgs.append({"role": r["role"], "content": r["content"]})

    messages = [{"role": "system", "content": system_msg}] + history_msgs + [{"role": "user", "content": question}]
    answer, err = call_openrouter(messages, max_tokens=650, temperature=0.4)

    if err:
        return jsonify({"success": False, "answer": f"⚠️ تعذر الحصول على رد حالياً: {err}"})

    if uid:
        now_t = datetime.now().strftime("%H:%M")
        conn = get_db()
        conn.execute(
            "INSERT INTO ai_messages (patient_id, role, content, time) VALUES (?, 'user', ?, ?)",
            (uid, question, now_t),
        )
        conn.execute(
            "INSERT INTO ai_messages (patient_id, role, content, time) VALUES (?, 'assistant', ?, ?)",
            (uid, answer, now_t),
        )
        conn.commit()
        conn.close()

    return jsonify({"success": True, "answer": answer})


@app.route("/api/ai/meal-analyze", methods=["POST"])
def analyze_meal():
    if "image" not in request.files:
        return jsonify({"success": False, "message": "ما وصلت أي صورة للسيرفر."})

    data_url = get_uploaded_image_data_url(request.files["image"])
    if not data_url:
        return jsonify({"success": False, "message": "ملف الصورة فارغ أو غير صالح."})

    uid = session.get("user_id")
    _, dialect_inst, patient_ctx = build_patient_medical_context(uid)
    user_note = str(request.form.get("note", "")).strip()

    prompt = (
        f"أنت أخصائي تغذية وطبيب سكري. {dialect_inst}\n"
        f"{patient_ctx}\n"
        f"ملاحظة المراجع عن الوجبة: {user_note or 'لا توجد'}\n\n"
        "حلل صورة الوجبة الغذائية المرفقة بدقة لمريض السكري. "
        "أجب بصيغة JSON فقط وبدون أي نص خارجي بالمفاتيح التالية:\n"
        '{"meal_name": "اسم الوجبة ومكوناتها الواضحة بالصورة", '
        '"glycemic_impact": "منخفض 🟢 أو متوسط 🟡 أو مرتفع 🔴", '
        '"carbs": "تقدير الكربوهيدرات بالغرام (مثلاً: 45 غرام)", '
        '"calories": "تقدير السعرات الحرارية (مثلاً: 420 سعرة)", '
        '"protein_fat": "تقدير البروتين والدهون وهل تسبب صعود متأخر", '
        '"insulin_dose": "تقدير وحدات الأنسولين السريع حسب معامل الكارب للمراجع أو الحصة المناسبة", '
        '"is_heavy_fat": true, '
        '"tips": "نصيحة عملية مختصرة باللهجة المحلية لتناول الوجبة بدون طفرة سكر"}'
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]

    raw, err = call_openrouter(messages, max_tokens=600, temperature=0.25)
    if err:
        return jsonify({"success": False, "message": err})

    try:
        parsed = extract_json_block(raw)
        return jsonify({
            "success": True,
            "meal_name": parsed.get("meal_name", "وجبة غذائية"),
            "glycemic_impact": parsed.get("glycemic_impact", "متوسط 🟡"),
            "carbs": parsed.get("carbs", "غير محدد"),
            "calories": parsed.get("calories", "غير محدد"),
            "protein_fat": parsed.get("protein_fat", "-"),
            "insulin_dose": parsed.get("insulin_dose", "حسب جدول الطبيب"),
            "is_heavy_fat": bool(parsed.get("is_heavy_fat", False)),
            "tips": parsed.get("tips", "يفضل قياس السكر بعد الأكل بساعتين."),
        })
    except Exception:
        return jsonify({
            "success": True,
            "meal_name": "نتيجة تحليل الوجبة",
            "glycemic_impact": "متوسط 🟡",
            "carbs": "موضح بالتفاصيل",
            "calories": "-",
            "protein_fat": "-",
            "insulin_dose": "راجع الطبيب لتحديد الجرعة الدقيقة",
            "is_heavy_fat": False,
            "tips": raw,
        })


@app.route("/api/ai/nutrition-label", methods=["POST"])
def analyze_nutrition_label():
    if "image" not in request.files:
        return jsonify({"success": False, "message": "اختار صورة جدول القيم الغذائية على العلبة أولاً."})

    data_url = get_uploaded_image_data_url(request.files["image"])
    if not data_url:
        return jsonify({"success": False, "message": "ملف الصورة فارغ أو غير صالح."})

    uid = session.get("user_id")
    _, dialect_inst, patient_ctx = build_patient_medical_context(uid)

    prompt = (
        f"أنت أخصائي تغذية لمرضى السكري. {dialect_inst}\n"
        f"{patient_ctx}\n\n"
        "اقرأ جدول القيم الغذائية (Nutrition Facts) وقائمة المكونات في صورة المنتج المعلب المرفقة، واكشف أي سكريات مخفية (مثل Maltodextrin, Dextrose, Corn Syrup, Sucrose). "
        "أجب بصيغة JSON فقط بالمفاتيح التالية:\n"
        '{"product_name": "اسم المنتج أو نوعه", '
        '"serving_size": "حجم الحصة الواحدة", '
        '"carbs": "الكربوهيدرات الكلية لكل حصة", '
        '"sugars": "كمية السكريات بالغرام", '
        '"hidden_sugars": "السكريات المخفية المكتشفة أو: لا توجد", '
        '"verdict": "آمن 🟢 أو باعتدال 🟡 أو تجنبه 🔴", '
        '"advice": "نصيحة واضحة للمراجع قبل شراء أو تناول المنتج"}'
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]

    raw, err = call_openrouter(messages, max_tokens=550, temperature=0.25)
    if err:
        return jsonify({"success": False, "message": err})

    try:
        parsed = extract_json_block(raw)
        return jsonify({
            "success": True,
            "product_name": parsed.get("product_name", "منتج غذائي معلب"),
            "serving_size": parsed.get("serving_size", "-"),
            "carbs": parsed.get("carbs", "-"),
            "sugars": parsed.get("sugars", "-"),
            "hidden_sugars": parsed.get("hidden_sugars", "غير محدد"),
            "verdict": parsed.get("verdict", "يرجى الانتباه للكمية"),
            "advice": parsed.get("advice", raw),
        })
    except Exception:
        return jsonify({
            "success": True,
            "product_name": "قراءة ملصق غذائي",
            "serving_size": "-",
            "carbs": "-",
            "sugars": "-",
            "hidden_sugars": "-",
            "verdict": "موضح بالتفاصيل",
            "advice": raw,
        })


@app.route("/api/ai/lab-analyze", methods=["POST"])
def analyze_lab_report():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "message": "يرجى تسجيل الدخول أولاً"}), 401

    if "image" not in request.files:
        return jsonify({"success": False, "message": "اختار صورة ورقة التحليل المختبري أولاً."})

    data_url = get_uploaded_image_data_url(request.files["image"])
    if not data_url:
        return jsonify({"success": False, "message": "ملف الصورة فارغ أو غير صالح."})

    _, dialect_inst, patient_ctx = build_patient_medical_context(uid)

    prompt = (
        f"أنت طبيب متخصص بالسكري والباطنية. {dialect_inst}\n"
        f"{patient_ctx}\n\n"
        "اقرأ ورقة التحليل المختبري في الصورة بدقة واشرحها للمريض ببساطة ووضوح. "
        "أجب بصيغة JSON فقط بالمفاتيح التالية:\n"
        '{"title": "نوع التحليل (مثلاً: تحليل السكر التراكمي HbA1c أو وظائف الكلى والدهون)", '
        '"summary": "شرح مبسط لأهم الأرقام الموجودة بالتحليل مقارنة بالمعدل الطبيعي", '
        '"abnormal": "القيم الصاعدة أو النازلة عن الطبيعي إن وجدت أو اكتب: جميع القيم ضمن الحدود الطبيعية", '
        '"advice": "نصيحة طبية مختصرة للمراجع"}'
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]

    raw, err = call_openrouter(messages, max_tokens=700, temperature=0.25)
    if err:
        return jsonify({"success": False, "message": err})

    try:
        parsed = extract_json_block(raw)
        title = parsed.get("title", "تحليل مختبري")
        summary = parsed.get("summary", raw)
        abnormal = parsed.get("abnormal", "يرجى مراجعة الطبيب")
        advice = parsed.get("advice", "اعرض التحليل على طبيبك المشرف.")
    except Exception:
        title = "قراءة تحليل مختبري"
        summary = raw
        abnormal = "-"
        advice = "يفضل مناقشة النتيجة مع طبيبك بالزيارة القادمة."

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute(
        "INSERT INTO lab_reports (patient_id, title, summary, abnormal, advice, date) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, title, summary, abnormal, advice, date_str),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "title": title,
        "summary": summary,
        "abnormal": abnormal,
        "advice": advice,
        "date": date_str,
    })


@app.route("/api/ai/med-analyze", methods=["POST"])
def analyze_medication_image():
    if "image" not in request.files:
        return jsonify({"success": False, "message": "اختار صورة علبة الدواء أو قلم الأنسولين أولاً."})

    data_url = get_uploaded_image_data_url(request.files["image"])
    if not data_url:
        return jsonify({"success": False, "message": "ملف الصورة فارغ أو غير صالح."})

    uid = session.get("user_id")
    _, dialect_inst, patient_ctx = build_patient_medical_context(uid)

    prompt = (
        f"أنت صيدلي إكلينيكي وطبيب سكري. {dialect_inst}\n"
        f"{patient_ctx}\n\n"
        "تعرف على علبة الدواء أو شريط الحبوب أو قلم الأنسولين الظاهر في الصورة، واشرحه للمريض ببساطة. "
        "أجب بصيغة JSON فقط بالمفاتيح التالية:\n"
        '{"med_name": "الاسم التجاري والتركيز الظاهر على العلبة", '
        '"active_ingredient": "المادة العلمية الفعالة وفئة العلاج", '
        '"usage": "الطريقة الشائعة لأخذه (قبل الأكل / بعد الأكل / طويل المفعول)", '
        '"hypo_risk": "هل يسبب هبوط سكر؟ (نعم - انتبه للهبوط / لا يسبب هبوطاً بمفرده)", '
        '"advice": "تنبيه مهم للمراجع حول هذا الدواء"}'
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]

    raw, err = call_openrouter(messages, max_tokens=550, temperature=0.25)
    if err:
        return jsonify({"success": False, "message": err})

    try:
        parsed = extract_json_block(raw)
        return jsonify({
            "success": True,
            "med_name": parsed.get("med_name", "دواء"),
            "active_ingredient": parsed.get("active_ingredient", "-"),
            "usage": parsed.get("usage", "-"),
            "hypo_risk": parsed.get("hypo_risk", "راجع طبيبك"),
            "advice": parsed.get("advice", raw),
        })
    except Exception:
        return jsonify({
            "success": True,
            "med_name": "قراءة علبة الدواء",
            "active_ingredient": "-",
            "usage": "-",
            "hypo_risk": "-",
            "advice": raw,
        })


@app.route("/api/lab-reports")
def get_my_lab_reports():
    uid = session.get("user_id")
    if not uid:
        return jsonify([]), 401
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, summary, abnormal, advice, date FROM lab_reports WHERE patient_id = ? ORDER BY id DESC",
        (uid,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ==============================================================================
# 11. مسارات الطبيب ولوحة إحصائيات العيادة
# ==============================================================================

@app.route("/api/doctor/patients")
def doctor_patients():
    if session.get("role") != "doctor":
        return jsonify([]), 403

    conn = get_db()
    pat_rows = conn.execute("SELECT * FROM users WHERE role = 'patient' ORDER BY id DESC").fetchall()

    patients_list = []
    for r in pat_rows:
        p = serialize_user(r)
        logs = [
            dict(l) for l in conn.execute(
                "SELECT id, val, timing, meds, bp, mood, mealName, ketones, date FROM logs WHERE patient_id = ? ORDER BY id DESC",
                (p["id"],),
            ).fetchall()
        ]
        labs = [
            dict(lb) for lb in conn.execute(
                "SELECT id, title, summary, abnormal, advice, date FROM lab_reports WHERE patient_id = ? ORDER BY id DESC",
                (p["id"],),
            ).fetchall()
        ]
        daily_rows = [
            dict(dr) for dr in conn.execute(
                "SELECT * FROM daily_trackers WHERE patient_id = ? ORDER BY track_date DESC LIMIT 14",
                (p["id"],),
            ).fetchall()
        ]
        neuro = conn.execute(
            "SELECT * FROM neuropathy_checks WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
            (p["id"],),
        ).fetchone()

        analytics = compute_patient_analytics(logs, p, daily_rows)
        latest_val = logs[0]["val"] if logs else None
        is_critical = bool(
            (latest_val is not None and (latest_val < 70 or latest_val > 250))
            or analytics["hasDangerousSwing"]
            or (logs and str(logs[0].get("ketones", "")) == "مرتفع")
        )

        p["logs"] = logs
        p["labReports"] = labs
        p["analytics"] = analytics
        p["latestNeuropathy"] = dict(neuro) if neuro else None
        p["latestVal"] = latest_val
        p["isCritical"] = is_critical
        patients_list.append(p)

    conn.close()
    patients_list.sort(key=lambda x: (0 if x["isCritical"] else 1, -x["id"]))
    return jsonify(patients_list)


@app.route("/api/doctor/create-patient", methods=["POST"])
def doctor_create_patient():
    if session.get("role") != "doctor":
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    age = int(data.get("age") or 40)
    weight = float(data.get("weight") or 70)
    height = float(data.get("height") or 170)
    d_type = str(data.get("diabetesType") or "النوع الثاني").strip()
    country_code = str(data.get("countryCode") or "+964").strip()
    raw_phone = str(data.get("emergencyPhone") or "07700000000").strip()
    norm_phone, cc = normalize_phone(raw_phone, country_code)
    c_info = detect_country_info(cc, norm_phone)

    target_g = int(data.get("targetGlucose") or 120)
    corr_f = int(data.get("correctionFactor") or 40)
    carb_r = int(data.get("carbRatio") or 15)

    if not name or not username or not password:
        return jsonify({"success": False, "message": "يرجى ملء الاسم واليوزر والرمز السري"})

    conn = get_db()
    exists = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        conn.close()
        return jsonify({"success": False, "message": "هذا اليوزر مستخدم مسبقاً، اختار غيره"})

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (
            username, password, role, name, phone, countryCode, countryName, dialect,
            age, weight, height, diabetesType, emergencyPhone, targetGlucose, correctionFactor, carbRatio
        )
        VALUES (?, ?, 'patient', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        username, password, name, norm_phone, cc, c_info["countryName"], c_info["dialect"],
        age, weight, height, d_type, raw_phone, target_g, corr_f, carb_r,
    ))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return jsonify({"success": True, "patient_id": new_id})


@app.route("/api/doctor/update-patient", methods=["POST"])
def doctor_update_patient():
    if session.get("role") != "doctor":
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    data = request.get_json(silent=True) or {}
    pid = data.get("patient_id")
    if not pid:
        return jsonify({"success": False, "message": "معرف المراجع مفقود"})

    cc = str(data.get("countryCode") or "+964").strip()
    c_info = detect_country_info(cc)

    conn = get_db()
    conn.execute("""
        UPDATE users
        SET name = ?, age = ?, weight = ?, height = ?, diabetesType = ?, blood = ?,
            emergencyPhone = ?, countryCode = ?, countryName = ?, dialect = ?
        WHERE id = ? AND role = 'patient'
    """, (
        str(data.get("name", "")).strip(),
        int(data.get("age") or 40),
        float(data.get("weight") or 70),
        float(data.get("height") or 170),
        str(data.get("diabetesType") or "النوع الثاني").strip(),
        str(data.get("blood") or "O+").strip(),
        str(data.get("emergencyPhone") or "").strip(),
        cc, c_info["countryName"], c_info["dialect"],
        pid,
    ))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/doctor/delete-patient/<int:patient_id>", methods=["DELETE"])
def doctor_delete_patient(patient_id):
    if session.get("role") != "doctor":
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    conn = get_db()
    tables = [
        "logs", "messages", "ai_messages", "lab_reports",
        "daily_trackers", "neuropathy_checks", "calibration_logs",
    ]
    for tbl in tables:
        conn.execute(f"DELETE FROM {tbl} WHERE patient_id = ?", (patient_id,))
    conn.execute("DELETE FROM users WHERE id = ? AND role = 'patient'", (patient_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/doctor/update-notes", methods=["POST"])
def doctor_update_notes():
    if session.get("role") != "doctor":
        return jsonify({"success": False}), 403

    data = request.get_json(silent=True) or {}
    pid = data.get("patient_id")
    notes = str(data.get("notes", ""))
    target_g = int(data.get("targetGlucose") or 120)
    corr_f = int(data.get("correctionFactor") or 40)
    carb_r = int(data.get("carbRatio") or 15)
    next_visit = str(data.get("nextVisitDate") or "").strip()

    conn = get_db()
    conn.execute("""
        UPDATE users
        SET doctorNotes = ?, targetGlucose = ?, correctionFactor = ?, carbRatio = ?, nextVisitDate = ?
        WHERE id = ?
    """, (notes, target_g, corr_f, carb_r, next_visit, pid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/doctor/reset-patient-password", methods=["POST"])
def doctor_reset_password():
    if session.get("role") != "doctor":
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    data = request.get_json(silent=True) or {}
    pid = data.get("patient_id")
    new_pass = str(data.get("new_password", "")).strip()
    if not pid or not new_pass:
        return jsonify({"success": False, "message": "يرجى إدخال الرمز السري الجديد"})

    conn = get_db()
    conn.execute("UPDATE users SET password = ? WHERE id = ? AND role = 'patient'", (new_pass, pid))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "تم تحديث الرمز السري للمراجع بنجاح"})


# ==============================================================================
# 12. تصدير السجل الكامل لملف Excel / CSV
# ==============================================================================

@app.route("/api/export/excel/<int:patient_id>")
def export_patient_excel(patient_id):
    uid = session.get("user_id")
    role = session.get("role")
    if not uid or (role == "patient" and uid != patient_id):
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    conn = get_db()
    pat = conn.execute("SELECT name, diabetesType, emergencyPhone FROM users WHERE id = ?", (patient_id,)).fetchone()
    logs = conn.execute(
        "SELECT date, val, timing, bp, meds, mood, mealName, ketones FROM logs WHERE patient_id = ? ORDER BY id DESC",
        (patient_id,),
    ).fetchall()
    conn.close()

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["اسم المراجع", pat["name"] if pat else "-", "نوع السكري", pat["diabetesType"] if pat else "-"])
    writer.writerow([])
    writer.writerow([
        "التاريخ والوقت", "قياس السكر (mg/dL)", "توقيت القياس", "ضغط الدم",
        "العلاج / الجرعة", "الحالة المزاجية", "الوجبة", "الكيتونات",
    ])

    for l in logs:
        writer.writerow([
            l["date"],
            l["val"],
            l["timing"],
            l["bp"] or "-",
            l["meds"] or "-",
            l["mood"] or "عادي",
            l["mealName"] or "-",
            l["ketones"] or "-",
        ])

    filename = f"sukkari_report_{patient_id}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ==============================================================================
# 13. مسارات المحادثة المباشرة مع الطبيب
# ==============================================================================

@app.route("/api/chat/<int:patient_id>", methods=["GET", "POST"])
def handle_chat(patient_id):
    uid = session.get("user_id")
    role = session.get("role")
    if not uid:
        return jsonify([]), 401

    conn = get_db()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if text:
            sender = "doctor" if role == "doctor" else "patient"
            time_str = datetime.now().strftime("%H:%M")
            conn.execute(
                "INSERT INTO messages (patient_id, sender, text, time) VALUES (?, ?, ?, ?)",
                (patient_id, sender, text, time_str),
            )
            conn.commit()
        conn.close()
        return jsonify({"success": True})

    rows = conn.execute(
        "SELECT id, sender, text, time FROM messages WHERE patient_id = ? ORDER BY id ASC",
        (patient_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ==============================================================================
# 14. نقطة تشغيل الخادم
# ==============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
