import os
import json
import base64
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, Response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sukkari_smart_secret_key_2026")

DB_PATH = os.environ.get("DB_PATH", "sukkari.db")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()


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
            lastFootCheck TEXT DEFAULT '',
            medReminderTime TEXT DEFAULT ''
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

    # ترقية الجداول القديمة إذا كانت موجودة مسبقاً
    existing_user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
    for col_name, col_def in [
        ("targetGlucose", "INTEGER DEFAULT 120"),
        ("correctionFactor", "INTEGER DEFAULT 40"),
        ("lastFootCheck", "TEXT DEFAULT ''"),
        ("medReminderTime", "TEXT DEFAULT ''"),
    ]:
        if col_name not in existing_user_cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

    existing_log_cols = [r[1] for r in cur.execute("PRAGMA table_info(logs)").fetchall()]
    if "bp" not in existing_log_cols:
        cur.execute("ALTER TABLE logs ADD COLUMN bp TEXT DEFAULT ''")

    # إنشاء الحسابات الافتراضية إذا كانت قاعدة البيانات فارغة
    count = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        cur.execute("""
            INSERT INTO users (username, password, role, name, age, gender, weight, height, blood, diabetesType, emergencyPhone, doctorNotes)
            VALUES (?, ?, 'doctor', ?, 45, 'ذكر', 80, 178, 'A+', '-', '07701234567', '')
        """, ("doctor", "123", "د. خالد السامرائي"))

        cur.execute("""
            INSERT INTO users (username, password, role, name, age, gender, weight, height, blood, diabetesType, emergencyPhone, doctorNotes, targetGlucose, correctionFactor)
            VALUES (?, ?, 'patient', ?, 42, 'ذكر', 75, 175, 'O+', 'النوع الثاني', '07701112233', 'الالتزام بالمشي نص ساعة يومياً وتقليل الخبز والتمن.', 120, 40)
        """, ("patient", "123", "أحمد علي"))
        pat_id = cur.lastrowid

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        sample_logs = [
            (pat_id, 140, "ورا الأكل", "-", "125/80", now_str),
            (pat_id, 122, "عشوائي", "-", "120/78", now_str),
            (pat_id, 115, "على الريك", "4 وحدات أنسولين", "118/75", now_str),
        ]
        cur.executemany("INSERT INTO logs (patient_id, val, timing, meds, bp, date) VALUES (?, ?, ?, ?, ?, ?)", sample_logs)

        cur.execute("""
            INSERT INTO messages (patient_id, sender, text, time)
            VALUES (?, 'doctor', 'هلا بيك أخ أحمد، قياساتك هالأسبوع زينة، استمر على نفس العلاج وبلغني بأي طارئ.', ?)
        """, (pat_id, datetime.now().strftime("%H:%M")))

    conn.commit()
    conn.close()


init_db()


def serialize_user(row):
    if not row:
        return None
    u = dict(row)
    u.pop("password", None)
    u["bmi"] = calc_bmi(u.get("weight", 0), u.get("height", 0))
    return u


def call_openrouter(messages, max_tokens=700, temperature=0.4):
    if not OPENROUTER_API_KEY:
        return None, "مفتاح الذكاء الاصطناعي (OPENROUTER_API_KEY) غير مضبوط بالسيرفر."

    models = ["google/gemini-2.0-flash-001", "openai/gpt-4o-mini"]
    last_error = "تعذر الاتصال بخدمة الذكاء الاصطناعي."

    for model in models:
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://sukkari-app.local",
                "X-Title": "Sukkari Smart Health"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
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


# ================= مسارات الواجهة و PWA =================

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
    data = {
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
                "purpose": "any maskable"
            }
        ]
    }
    return jsonify(data)


@app.route("/sw.js")
def service_worker():
    js = """
    const CACHE_NAME = 'sukkari-pwa-v1';
    self.addEventListener('install', (event) => {
        self.skipWaiting();
    });
    self.addEventListener('activate', (event) => {
        event.waitUntil(clients.claim());
    });
    self.addEventListener('fetch', (event) => {
        if (event.request.method !== 'GET') return;
        event.respondWith(
            fetch(event.request).catch(() => caches.match(event.request))
        );
    });
    """
    return Response(js, mimetype="application/javascript")


# ================= مسارات المصادقة والحساب =================

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
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, password)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"success": False, "message": "اليوزر أو الرمز السري غير صحيح"})

    session["user_id"] = row["id"]
    session["role"] = row["role"]
    return jsonify({"success": True, "user": serialize_user(row)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/patient/update-profile", methods=["POST"])
def update_patient_profile():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "message": "غير مسجل الدخول"}), 401

    data = request.get_json(silent=True) or {}
    age = int(data.get("age") or 40)
    weight = float(data.get("weight") or 70)
    height = float(data.get("height") or 170)
    phone = str(data.get("emergencyPhone") or "").strip()
    med_time = str(data.get("medReminderTime") or "").strip()

    conn = get_db()
    conn.execute("""
        UPDATE users
        SET age = ?, weight = ?, height = ?, emergencyPhone = ?, medReminderTime = ?
        WHERE id = ?
    """, (age, weight, height, phone, med_time, uid))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()

    return jsonify({"success": True, "user": serialize_user(row)})


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


# ================= مسارات القياسات =================

@app.route("/api/logs", methods=["GET", "POST"])
def handle_logs():
    uid = session.get("user_id")
    if not uid:
        return jsonify([]), 401

    conn = get_db()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        val = int(float(data.get("val", 0)))
        timing = str(data.get("timing", "عشوائي")).strip()
        meds = str(data.get("meds", "-")).strip() or "-"
        bp = str(data.get("bp", "")).strip()
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        conn.execute(
            "INSERT INTO logs (patient_id, val, timing, meds, bp, date) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, val, timing, meds, bp, date_str)
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    rows = conn.execute(
        "SELECT id, val, timing, meds, bp, date FROM logs WHERE patient_id = ? ORDER BY id DESC",
        (uid,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ================= مسارات الذكاء الاصطناعي (أكل، تحاليل، مساعد) =================

@app.route("/api/ai/meal-analyze", methods=["POST"])
def analyze_meal():
    if "image" not in request.files:
        return jsonify({"success": False, "message": "ما وصلت أي صورة للسيرفر."})

    img_bytes = request.files["image"].read()
    b64_img = base64.b64encode(img_bytes).decode("utf-8")

    prompt = (
        "أنت أخصائي تغذية وطبيب سكري عراقي. حلل صورة الوجبة الغذائية المرفقة لمريض سكري. "
        "أجب بصيغة JSON فقط وبدون أي نص خارجي وباللهجة العراقية الواضحة بالمفاتيح التالية: "
        '{"meal_name": "اسم الوجبة ومكوناتها", "carbs": "تقدير الكربوهيدرات بالغرام", '
        '"calories": "تقدير السعرات الحرارية", "insulin_dose": "تقدير وحدات الأنسولين السريع المقترحة أو نصيحة الكمية", '
        '"tips": "نصيحة غذائية مختصرة لتجنب صعود السكر"}'
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
        ]
    }]

    raw, err = call_openrouter(messages, max_tokens=500, temperature=0.3)
    if err:
        return jsonify({"success": False, "message": err})

    try:
        parsed = extract_json_block(raw)
        return jsonify({
            "success": True,
            "meal_name": parsed.get("meal_name", "وجبة غذائية"),
            "carbs": parsed.get("carbs", "غير محدد"),
            "calories": parsed.get("calories", "غير محدد"),
            "insulin_dose": parsed.get("insulin_dose", "حسب جدول الطبيب"),
            "tips": parsed.get("tips", "يفضل قياس السكر بعد الأكل بساعتين.")
        })
    except Exception:
        return jsonify({
            "success": True,
            "meal_name": "نتيجة تحليل الوجبة",
            "carbs": "موضح بالتفاصيل",
            "calories": "-",
            "insulin_dose": "راجع الطبيب لتحديد الجرعة الدقيقة",
            "tips": raw
        })


@app.route("/api/ai/lab-analyze", methods=["POST"])
def analyze_lab_report():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "message": "يرجى تسجيل الدخول أولاً"}), 401

    if "image" not in request.files:
        return jsonify({"success": False, "message": "اختار صورة ورقة التحليل المختبري أولاً."})

    img_bytes = request.files["image"].read()
    b64_img = base64.b64encode(img_bytes).decode("utf-8")

    prompt = (
        "أنت طبيب عراقي متخصص بالسكري والباطنية. اقرأ ورقة التحليل المختبري في الصورة واشرحها للمريض باللهجة العراقية البسيطة والمطمئنة. "
        "أجب بصيغة JSON فقط بالمفاتيح التالية: "
        '{"title": "نوع التحليل (مثلاً: تحليل السكر التراكمي HbA1c أو وظائف الكلى)", '
        '"summary": "شرح مبسط لأهم الأرقام الموجودة بالتحليل", '
        '"abnormal": "القيم الصاعدة أو النازلة عن الطبيعي إن وجدت أو اكتب: جميع القيم ضمن الحدود الطبيعية", '
        '"advice": "نصيحة طبية مختصرة للمراجع"}'
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
        ]
    }]

    raw, err = call_openrouter(messages, max_tokens=650, temperature=0.3)
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
        advice = "يفضل مناقشة النتيجة ويه دكتورك بالزيارة القادمة."

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute(
        "INSERT INTO lab_reports (patient_id, title, summary, abnormal, advice, date) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, title, summary, abnormal, advice, date_str)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "title": title,
        "summary": summary,
        "abnormal": abnormal,
        "advice": advice,
        "date": date_str
    })


@app.route("/api/lab-reports")
def get_my_lab_reports():
    uid = session.get("user_id")
    if not uid:
        return jsonify([]), 401
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, summary, abnormal, advice, date FROM lab_reports WHERE patient_id = ? ORDER BY id DESC",
        (uid,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/ai/assistant", methods=["POST"])
def ai_assistant():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"answer": "اكتب سؤالك عيني حتى أجاوبك."})

    system_msg = (
        "أنت مساعد طبي ذكي متخصص بمرض السكري ومضاعفاته وتغذيته، تتحدث باللهجة العراقية المهذبة والواضحة والمطمئنة. "
        "أعطِ إجابات عملية مختصرة ومنظمة، ونبّه المريض لمراجعة طبيبه في الحالات الطارئة."
    )

    answer, err = call_openrouter([
        {"role": "system", "content": system_msg},
        {"role": "user", "content": question}
    ], max_tokens=550, temperature=0.45)

    if err:
        return jsonify({"answer": f"⚠️ تعذر الحصول على رد حالياً: {err}"})
    return jsonify({"answer": answer})


# ================= مسارات الطبيب =================

@app.route("/api/doctor/patients")
def doctor_patients():
    if session.get("role") != "doctor":
        return jsonify([]), 403

    conn = get_db()
    pat_rows = conn.execute("SELECT * FROM users WHERE role = 'patient' ORDER BY id DESC").fetchall()

    patients_list = []
    for r in pat_rows:
        p = serialize_user(r)
        logs = conn.execute(
            "SELECT id, val, timing, meds, bp, date FROM logs WHERE patient_id = ? ORDER BY id DESC",
            (p["id"],)
        ).fetchall()
        labs = conn.execute(
            "SELECT id, title, summary, abnormal, advice, date FROM lab_reports WHERE patient_id = ? ORDER BY id DESC",
            (p["id"],)
        ).fetchall()

        p["logs"] = [dict(l) for l in logs]
        p["labReports"] = [dict(lb) for lb in labs]

        latest_val = p["logs"][0]["val"] if p["logs"] else None
        p["isCritical"] = bool(latest_val is not None and (latest_val < 70 or latest_val > 250))
        p["latestVal"] = latest_val
        patients_list.append(p)

    conn.close()

    # ترتيب الحالات الحرجة بأعلى القائمة تلقائياً
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
    phone = str(data.get("emergencyPhone") or "07700000000").strip()
    target_g = int(data.get("targetGlucose") or 120)
    corr_f = int(data.get("correctionFactor") or 40)

    if not name or not username or not password:
        return jsonify({"success": False, "message": "يرجى ملء الاسم واليوزر والرمز السري"})

    conn = get_db()
    exists = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        conn.close()
        return jsonify({"success": False, "message": "هذا اليوزر مستخدم مسبقاً، اختار غيره"})

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (username, password, role, name, age, weight, height, diabetesType, emergencyPhone, targetGlucose, correctionFactor)
        VALUES (?, ?, 'patient', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (username, password, name, age, weight, height, d_type, phone, target_g, corr_f))
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

    conn = get_db()
    conn.execute("""
        UPDATE users
        SET name = ?, age = ?, weight = ?, height = ?, diabetesType = ?, blood = ?, emergencyPhone = ?, targetGlucose = ?, correctionFactor = ?
        WHERE id = ? AND role = 'patient'
    """, (
        str(data.get("name", "")).strip(),
        int(data.get("age") or 40),
        float(data.get("weight") or 70),
        float(data.get("height") or 170),
        str(data.get("diabetesType") or "النوع الثاني").strip(),
        str(data.get("blood") or "O+").strip(),
        str(data.get("emergencyPhone") or "").strip(),
        int(data.get("targetGlucose") or 120),
        int(data.get("correctionFactor") or 40),
        pid
    ))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/doctor/delete-patient/<int:patient_id>", methods=["DELETE"])
def doctor_delete_patient(patient_id):
    if session.get("role") != "doctor":
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    conn = get_db()
    conn.execute("DELETE FROM logs WHERE patient_id = ?", (patient_id,))
    conn.execute("DELETE FROM messages WHERE patient_id = ?", (patient_id,))
    conn.execute("DELETE FROM lab_reports WHERE patient_id = ?", (patient_id,))
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

    conn = get_db()
    conn.execute(
        "UPDATE users SET doctorNotes = ?, targetGlucose = ?, correctionFactor = ? WHERE id = ?",
        (notes, target_g, corr_f, pid)
    )
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


# ================= مسارات المحادثة =================

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
                (patient_id, sender, text, time_str)
            )
            conn.commit()
        conn.close()
        return jsonify({"success": True})

    rows = conn.execute(
        "SELECT id, sender, text, time FROM messages WHERE patient_id = ? ORDER BY id ASC",
        (patient_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
