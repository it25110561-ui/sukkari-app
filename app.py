import os
import json
import base64
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, Response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sukkari_secret_key_2026_iraq")
DB_PATH = os.environ.get("DB_PATH", "sukkari.db")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(cursor, table_name, column_name, column_def):
    cursor.execute(f"PRAGMA table_info({table_name})")
    cols = [row[1] for row in cursor.fetchall()]
    if column_name not in cols:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            age INTEGER DEFAULT 40,
            weight REAL DEFAULT 75,
            height REAL DEFAULT 170,
            bmi REAL DEFAULT 26.0,
            diabetes_type TEXT DEFAULT 'النوع الثاني',
            gender TEXT DEFAULT 'ذكر',
            blood TEXT DEFAULT 'O+',
            emergency_phone TEXT DEFAULT '07700000000',
            doctor_notes TEXT DEFAULT '',
            target_glucose INTEGER DEFAULT 110,
            correction_factor REAL DEFAULT 50
        )
    """)

    ensure_column(c, "users", "target_glucose", "INTEGER DEFAULT 110")
    ensure_column(c, "users", "correction_factor", "REAL DEFAULT 50")

    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            val INTEGER NOT NULL,
            timing TEXT NOT NULL,
            meds TEXT DEFAULT '-',
            bp_sys INTEGER DEFAULT NULL,
            bp_dia INTEGER DEFAULT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY(patient_id) REFERENCES users(id)
        )
    """)

    ensure_column(c, "logs", "bp_sys", "INTEGER DEFAULT NULL")
    ensure_column(c, "logs", "bp_dia", "INTEGER DEFAULT NULL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            text TEXT NOT NULL,
            time TEXT NOT NULL,
            FOREIGN KEY(patient_id) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS lab_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            items_json TEXT NOT NULL,
            advice TEXT DEFAULT '',
            date TEXT NOT NULL,
            FOREIGN KEY(patient_id) REFERENCES users(id)
        )
    """)

    # حسابات تجريبية افتراضية إذا كانت القاعدة جديدة
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO users (username, password, name, role, age, weight, height, bmi, diabetes_type, gender, blood, emergency_phone, doctor_notes, target_glucose, correction_factor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("doctor", "123", "د. خالد السامرائي", "doctor", 45, 80, 178, 25.2, "-", "ذكر", "A+", "07701110000", "", 110, 50))

        c.execute("""
            INSERT INTO users (username, password, name, role, age, weight, height, bmi, diabetes_type, gender, blood, emergency_phone, doctor_notes, target_glucose, correction_factor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("patient", "123", "أحمد علي", "patient", 42, 75, 175, 24.5, "النوع الثاني", "ذكر", "O+", "07701112233", "الالتزام بالمشي نص ساعة يومياً وتقليل الخبز والتمن.", 110, 50))
        pat_id = c.lastrowid

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        sample_logs = [
            (pat_id, 140, "ورا الأكل", "-", 125, 80, now_str),
            (pat_id, 122, "عشوائي", "-", 120, 78, now_str),
            (pat_id, 115, "على الريك", "4 وحدات", 118, 76, now_str),
        ]
        c.executemany("INSERT INTO logs (patient_id, val, timing, meds, bp_sys, bp_dia, date) VALUES (?, ?, ?, ?, ?, ?, ?)", sample_logs)

        c.execute("INSERT INTO messages (patient_id, sender, text, time) VALUES (?, ?, ?, ?)",
                  (pat_id, "doctor", "هلا أحمد، قياساتك زينة بس حاول تقلل النشويات بالعشا.", "10:30 ص"))

    conn.commit()
    conn.close()


init_db()


def calc_bmi(weight, height):
    try:
        w = float(weight)
        h_m = float(height) / 100.0
        if w > 0 and h_m > 0:
            return round(w / (h_m * h_m), 1)
    except Exception:
        pass
    return 0.0


def serialize_user(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"],
        "role": row["role"],
        "age": row["age"],
        "weight": row["weight"],
        "height": row["height"],
        "bmi": row["bmi"],
        "diabetesType": row["diabetes_type"],
        "gender": row["gender"],
        "blood": row["blood"],
        "emergencyPhone": row["emergency_phone"],
        "doctorNotes": row["doctor_notes"] or "",
        "targetGlucose": row["target_glucose"] if row["target_glucose"] else 110,
        "correctionFactor": row["correction_factor"] if row["correction_factor"] is not None else 50,
    }


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return jsonify({"success": False, "message": "غير مصرح، سجل دخولك أولاً"}), 401
            if role and session.get("role") != role:
                return jsonify({"success": False, "message": "صلاحية غير كافية"}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator


def call_openrouter(messages, max_tokens=700, json_mode=False):
    if not OPENROUTER_API_KEY:
        return None, "مفتاح الذكاء الاصطناعي (OPENROUTER_API_KEY) غير مضاف بإعدادات السيرفر."

    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://sukkari.app",
            "X-Title": "Sukkari Smart Platform",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return content, None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return None, f"خطأ من خدمة الذكاء ({e.code}): {err_body[:140]}"
    except Exception as e:
        return None, f"تعذر الاتصال بخدمة الذكاء: {str(e)}"


# ==================== PWA Routes ====================
@app.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": "سُكّري الذكي - متابعة السكري",
        "short_name": "سُكّري",
        "description": "منصتك الذكية لمتابعة السكر والتواصل ويه دكتورك",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#059669",
        "dir": "rtl",
        "lang": "ar",
        "icons": [
            {
                "src": "/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    }
    return jsonify(manifest)


@app.route("/icon.svg")
def pwa_icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
      <rect width="512" height="512" rx="112" fill="#059669"/>
      <path d="M256 416s-144-92-144-200c0-53 43-96 96-96 31 0 58 15 76 38 18-23 45-38 76-38 53 0 96 43 96 96 0 108-144 200-144 200z" fill="#ffffff" opacity="0.2"/>
      <polyline points="96,264 176,264 212,184 256,336 296,224 328,264 416,264" fill="none" stroke="#ffffff" stroke-width="28" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>"""
    return Response(svg, mimetype="image/svg+xml")


@app.route("/sw.js")
def pwa_sw():
    sw_code = """
    const CACHE_NAME = 'sukkari-v1';
    self.addEventListener('install', (e) => {
        self.skipWaiting();
    });
    self.addEventListener('activate', (e) => {
        e.waitUntil(clients.claim());
    });
    self.addEventListener('fetch', (e) => {
        if (e.request.method !== 'GET') return;
        e.respondWith(
            fetch(e.request).catch(() => caches.match(e.request))
        );
    });
    """
    return Response(sw_code, mimetype="application/javascript")


# ==================== Main & Auth Routes ====================
@app.route("/")
def index():
    return render_template("index.html")


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
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password)).fetchone()
    conn.close()
    if not row:
        return jsonify({"success": False, "message": "اسم المستخدم أو الرمز السري غير صحيح"}), 401
    session["user_id"] = row["id"]
    session["role"] = row["role"]
    return jsonify({"success": True, "user": serialize_user(row)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})


# ==================== Patient Routes ====================
@app.route("/api/logs", methods=["GET"])
@login_required()
def get_logs():
    uid = session["user_id"]
    conn = get_db()
    rows = conn.execute("SELECT * FROM logs WHERE patient_id = ? ORDER BY id DESC", (uid,)).fetchall()
    conn.close()
    return jsonify([
        {
            "id": r["id"],
            "val": r["val"],
            "timing": r["timing"],
            "meds": r["meds"],
            "bp_sys": r["bp_sys"],
            "bp_dia": r["bp_dia"],
            "date": r["date"],
        }
        for r in rows
    ])


@app.route("/api/logs", methods=["POST"])
@login_required()
def add_log():
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    try:
        val = int(float(data.get("val")))
    except Exception:
        return jsonify({"success": False, "message": "قيمة السكر غير صالحة"}), 400

    timing = (data.get("timing") or "عشوائي").strip()
    meds = (data.get("meds") or "-").strip() or "-"
    bp_sys = data.get("bp_sys")
    bp_dia = data.get("bp_dia")
    try:
        bp_sys = int(bp_sys) if bp_sys not in (None, "", 0, "0") else None
        bp_dia = int(bp_dia) if bp_dia not in (None, "", 0, "0") else None
    except Exception:
        bp_sys, bp_dia = None, None

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute(
        "INSERT INTO logs (patient_id, val, timing, meds, bp_sys, bp_dia, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uid, val, timing, meds, bp_sys, bp_dia, now_str),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "val": val})


@app.route("/api/patient/update-profile", methods=["POST"])
@login_required("patient")
def update_patient_profile():
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "message": "المستخدم غير موجود"}), 404

    age = int(data.get("age") or row["age"])
    weight = float(data.get("weight") or row["weight"])
    height = float(data.get("height") or row["height"])
    phone = (data.get("emergencyPhone") or row["emergency_phone"]).strip()
    bmi = calc_bmi(weight, height)

    conn.execute(
        "UPDATE users SET age = ?, weight = ?, height = ?, bmi = ?, emergency_phone = ? WHERE id = ?",
        (age, weight, height, bmi, phone, uid),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return jsonify({"success": True, "user": serialize_user(updated)})


@app.route("/api/patient/lab-reports", methods=["GET"])
@login_required()
def get_patient_lab_reports():
    uid = session["user_id"]
    conn = get_db()
    rows = conn.execute("SELECT * FROM lab_reports WHERE patient_id = ? ORDER BY id DESC", (uid,)).fetchall()
    conn.close()
    return jsonify([
        {
            "id": r["id"],
            "title": r["title"],
            "summary": r["summary"],
            "items": json.loads(r["items_json"] or "[]"),
            "advice": r["advice"],
            "date": r["date"],
        }
        for r in rows
    ])


# ==================== AI Routes ====================
@app.route("/api/ai/meal-analyze", methods=["POST"])
@login_required()
def ai_meal_analyze():
    if "image" not in request.files:
        return jsonify({"success": False, "message": "ما وصلت صورة للأكل"}), 400
    img_bytes = request.files["image"].read()
    b64 = base64.b64encode(img_bytes).decode("utf-8")

    prompt = (
        "أنت أخصائي تغذية وطبيب سكري عراقي. حلل صورة الوجبة وأرجع النتيجة بصيغة JSON فقط بهذه الحقول بالضبط باللهجة العراقية الواضحة:\n"
        '{"meal_name": "اسم الأكلة ومكوناتها", "carbs": "كمية الكربوهيدرات بالغرام", '
        '"calories": "السعرات الحرارية التقريبية", "insulin_dose": "تقدير وحدات الأنسولين السريع المقترحة مع التنبيه لمراجعة الطبيب", '
        '"tips": "نصيحة غذائية مختصرة لمريض السكري"}'
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }
    ]
    raw, err = call_openrouter(messages, max_tokens=500, json_mode=True)
    if err:
        return jsonify({"success": False, "message": err})
    try:
        parsed = json.loads(raw)
        return jsonify({
            "success": True,
            "meal_name": parsed.get("meal_name", "وجبة غذائية"),
            "carbs": parsed.get("carbs", "غير محدد"),
            "calories": parsed.get("calories", "غير محدد"),
            "insulin_dose": parsed.get("insulin_dose", "استشر طبيبك لتحديد الجرعة"),
            "tips": parsed.get("tips", "يفضل قياس السكر بعد الأكل بساعتين."),
        })
    except Exception:
        return jsonify({"success": False, "message": "تعذر قراءة تحليل الوجبة، حاول مرة ثانية."})


@app.route("/api/ai/lab-analyze", methods=["POST"])
@login_required()
def ai_lab_analyze():
    if "image" not in request.files:
        return jsonify({"success": False, "message": "اختار صورة ورقة التحليل بالبداية"}), 400

    img_bytes = request.files["image"].read()
    b64 = base64.b64encode(img_bytes).decode("utf-8")

    prompt = (
        "أنت طبيب عراقي متخصص بالسكري والباطنية. اقرأ ورقة التحاليل المختبرية في الصورة واشرحها للمراجع باللهجة العراقية البسيطة.\n"
        "أرجع JSON فقط بهذا الشكل الدقيق:\n"
        '{"title": "عنوان التحليل (مثلاً: فحص التراكمي ووظائف الكلى)", '
        '"summary": "ملخص عام لحالة التحاليل بسطرين", '
        '"items": [{"name": "اسم الفحص بالعربي والانكليزي", "value": "النتيجة الرقمية", "status": "طبيعي أو مرتفع أو منخفض", "note": "شرح مبسط"}], '
        '"advice": "توصية طبية مختصرة للمراجع"}'
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }
    ]
    raw, err = call_openrouter(messages, max_tokens=800, json_mode=True)
    if err:
        return jsonify({"success": False, "message": err})

    try:
        parsed = json.loads(raw)
        title = parsed.get("title", "تحليل مختبري")
        summary = parsed.get("summary", "تمت قراءة التحليل.")
        items = parsed.get("items", [])
        advice = parsed.get("advice", "يرجى عرض التحليل على الدكتور المشرف.")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        uid = session["user_id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO lab_reports (patient_id, title, summary, items_json, advice, date) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, title, summary, json.dumps(items, ensure_ascii=False), advice, now_str),
        )
        rep_id = cur.lastrowid
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "report": {
                "id": rep_id,
                "title": title,
                "summary": summary,
                "items": items,
                "advice": advice,
                "date": now_str,
            },
        })
    except Exception:
        return jsonify({"success": False, "message": "ما كدرنا نقرأ ورقة التحليل بوضوح، تأكد أن الصورة واضحة وحاول مرة ثانية."})


@app.route("/api/ai/assistant", methods=["POST"])
@login_required()
def ai_assistant():
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "اكتب سؤالك عيني حتى أجاوبك."})

    messages = [
        {
            "role": "system",
            "content": (
                "أنت مساعد طبي ذكي متخصص بمرض السكري وتبعاته، تتحدث باللهجة العراقية الودودة والمحترمة. "
                "إجاباتك دقيقة طبياً، مختصرة، ومرتبة بنقاط واضحة، وتنبه دائماً لمراجعة الطبيب في الحالات الطارئة."
            ),
        },
        {"role": "user", "content": q},
    ]
    ans, err = call_openrouter(messages, max_tokens=550, json_mode=False)
    if err:
        return jsonify({"answer": f"⚠️ {err}"})
    return jsonify({"answer": ans})


# ==================== Doctor Routes ====================
@app.route("/api/doctor/patients", methods=["GET"])
@login_required("doctor")
def doctor_get_patients():
    conn = get_db()
    users = conn.execute("SELECT * FROM users WHERE role = 'patient' ORDER BY id DESC").fetchall()
    result = []
    for u in users:
        pid = u["id"]
        logs = conn.execute("SELECT * FROM logs WHERE patient_id = ? ORDER BY id DESC", (pid,)).fetchall()
        labs = conn.execute("SELECT * FROM lab_reports WHERE patient_id = ? ORDER BY id DESC", (pid,)).fetchall()

        logs_list = [
            {
                "id": l["id"],
                "val": l["val"],
                "timing": l["timing"],
                "meds": l["meds"],
                "bp_sys": l["bp_sys"],
                "bp_dia": l["bp_dia"],
                "date": l["date"],
            }
            for l in logs
        ]

        critical_alert = None
        if logs_list:
            latest_val = logs_list[0]["val"]
            if latest_val < 70:
                critical_alert = {"type": "low", "val": latest_val, "label": f"هبوط حاد ({latest_val})"}
            elif latest_val >= 250:
                critical_alert = {"type": "high", "val": latest_val, "label": f"ارتفاع خطر ({latest_val})"}

        p_obj = serialize_user(u)
        p_obj["logs"] = logs_list
        p_obj["criticalAlert"] = critical_alert
        p_obj["labReports"] = [
            {
                "id": lb["id"],
                "title": lb["title"],
                "summary": lb["summary"],
                "items": json.loads(lb["items_json"] or "[]"),
                "advice": lb["advice"],
                "date": lb["date"],
            }
            for lb in labs
        ]
        result.append(p_obj)

    conn.close()
    # ترتيب الحالات الحرجة بأعلى القائمة تلقائياً
    result.sort(key=lambda x: 0 if x["criticalAlert"] else 1)
    return jsonify(result)


@app.route("/api/doctor/update-notes", methods=["POST"])
@login_required("doctor")
def doctor_update_notes():
    data = request.get_json(silent=True) or {}
    pid = data.get("patient_id")
    notes = data.get("notes", "")
    conn = get_db()
    conn.execute("UPDATE users SET doctor_notes = ? WHERE id = ? AND role = 'patient'", (notes, pid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/doctor/update-patient-info", methods=["POST"])
@login_required("doctor")
def doctor_update_patient_info():
    data = request.get_json(silent=True) or {}
    pid = data.get("patient_id")
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'patient'", (pid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "message": "المراجع غير موجود"}), 404

    name = (data.get("name") or row["name"]).strip()
    age = int(data.get("age") or row["age"])
    weight = float(data.get("weight") or row["weight"])
    height = float(data.get("height") or row["height"])
    bmi = calc_bmi(weight, height)
    d_type = (data.get("diabetesType") or row["diabetes_type"]).strip()
    gender = (data.get("gender") or row["gender"]).strip()
    blood = (data.get("blood") or row["blood"]).strip()
    phone = (data.get("emergencyPhone") or row["emergency_phone"]).strip()
    target_g = int(data.get("targetGlucose") or 110)
    corr_f = float(data.get("correctionFactor") or 50)

    conn.execute("""
        UPDATE users
        SET name = ?, age = ?, weight = ?, height = ?, bmi = ?, diabetes_type = ?,
            gender = ?, blood = ?, emergency_phone = ?, target_glucose = ?, correction_factor = ?
        WHERE id = ?
    """, (name, age, weight, height, bmi, d_type, gender, blood, phone, target_g, corr_f, pid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/doctor/delete-patient", methods=["POST"])
@login_required("doctor")
def doctor_delete_patient():
    data = request.get_json(silent=True) or {}
    pid = data.get("patient_id")
    conn = get_db()
    conn.execute("DELETE FROM logs WHERE patient_id = ?", (pid,))
    conn.execute("DELETE FROM messages WHERE patient_id = ?", (pid,))
    conn.execute("DELETE FROM lab_reports WHERE patient_id = ?", (pid,))
    conn.execute("DELETE FROM users WHERE id = ? AND role = 'patient'", (pid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/doctor/reset-patient-password", methods=["POST"])
@login_required("doctor")
def doctor_reset_patient_password():
    data = request.get_json(silent=True) or {}
    pid = data.get("patient_id")
    new_pass = (data.get("new_password") or "").strip()
    if not new_pass:
        return jsonify({"success": False, "message": "اكتب الرمز السري الجديد"}), 400
    conn = get_db()
    conn.execute("UPDATE users SET password = ? WHERE id = ? AND role = 'patient'", (new_pass, pid))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "تم تحديث الرمز السري للمراجع بنجاح"})


@app.route("/api/doctor/create-patient", methods=["POST"])
@login_required("doctor")
def doctor_create_patient():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not name or not username or not password:
        return jsonify({"success": False, "message": "املأ الاسم واليوزر والرمز السري"}), 400

    age = int(data.get("age") or 40)
    weight = float(data.get("weight") or 70)
    height = float(data.get("height") or 170)
    bmi = calc_bmi(weight, height)
    d_type = (data.get("diabetesType") or "النوع الثاني").strip()
    phone = (data.get("emergencyPhone") or "07700000000").strip()
    gender = (data.get("gender") or "ذكر").strip()
    blood = (data.get("blood") or "O+").strip()
    target_g = int(data.get("targetGlucose") or 110)
    corr_f = float(data.get("correctionFactor") or 50)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (username, password, name, role, age, weight, height, bmi, diabetes_type, gender, blood, emergency_phone, doctor_notes, target_glucose, correction_factor)
            VALUES (?, ?, ?, 'patient', ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
        """, (username, password, name, age, weight, height, bmi, d_type, gender, blood, phone, target_g, corr_f))
        pid = cur.lastrowid
        conn.commit()
        return jsonify({"success": True, "patient_id": pid})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "اسم المستخدم موجود مسبقاً، اختار غيره"}), 400
    finally:
        conn.close()


# ==================== Chat Routes ====================
@app.route("/api/chat/<int:patient_id>", methods=["GET"])
@login_required()
def get_chat(patient_id):
    if session.get("role") == "patient" and session.get("user_id") != patient_id:
        return jsonify([]), 403
    conn = get_db()
    rows = conn.execute("SELECT * FROM messages WHERE patient_id = ? ORDER BY id ASC", (patient_id,)).fetchall()
    conn.close()
    return jsonify([{"sender": r["sender"], "text": r["text"], "time": r["time"]} for r in rows])


@app.route("/api/chat/<int:patient_id>", methods=["POST"])
@login_required()
def post_chat(patient_id):
    if session.get("role") == "patient" and session.get("user_id") != patient_id:
        return jsonify({"success": False}), 403
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False}), 400
    sender = session.get("role", "patient")
    time_str = datetime.now().strftime("%I:%M %p")
    conn = get_db()
    conn.execute("INSERT INTO messages (patient_id, sender, text, time) VALUES (?, ?, ?, ?)", (patient_id, sender, text, time_str))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
