from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import time

GENAI_IMPORT_ERROR = ""
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except Exception as import_err:
    HAS_GENAI = False
    GENAI_IMPORT_ERROR = str(import_err)

app = Flask(__name__)
app.secret_key = 'sukari_secret_key_iraq_2026'

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'diabetes_db.sqlite')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'timeout': 30}}
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db = SQLAlchemy(app)
_db_ready = False

# ---------------- إعدادات جمناي (Gemini AI) ----------------
DIABETES_SYSTEM_PROMPT = (
    "أنت مساعد طبي ذكي متخصص حصراً في مرض السكري (النوع الأول، النوع الثاني، وسكري الحمل) ضمن منصة سُكّري الذكي.\n"
    "قواعد صارمة يجب الالتزام بها في كل رد:\n"
    "1. أجب باللهجة العراقية الودودة والمبسطة والواضحة للمريض.\n"
    "2. اختصاصك محصور بمرض السكري فقط: قياسات السكر، أعراض الهبوط والارتفاع وطرق التصرف الفوري، التغذية المناسبة لمرضى السكري، الكربوهيدرات، الأنسولين وأدوية السكري، الرياضة، الفحص التراكمي (HbA1c)، والوقاية من مضاعفات السكري.\n"
    "3. إذا سأل المريض عن أي موضوع خارج مرض السكري (مثل: برمجة، طبخ عام غير مرتبط بالسكري، رياضة عامة، سياسة، أسئلة ثقافية، أو أمراض أخرى لا علاقة لها بالسكري)، اعتذر بلطف باللهجة العراقية وقل له: عذراً عيني، أني مساعد مخصص فقط للإجابة عن الأسئلة المتعلقة بمرض السكري وتنظيمه. اسألني أي شي يخص السكر وأني حاضر!\n"
    "4. في حالات الطوارئ أو القراءات الخطيرة جداً (هبوط حاد أقل من 70 أو ارتفاع شديد فوق 250 مع أعراض)، نبه المريض فوراً للتصرف الآمن واستخدام زر الطوارئ SOS أو مراجعة الطبيب فوراً.\n"
    "5. لا تقم بتشخيص أمراض جديدة أو تغيير جرعات الأدوية بشكل نهائي، وذكّر المريض دائماً باستشارة دكتوره المشرف."
)

# ---------------- نماذج قاعدة البيانات (Models) ----------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    age = db.Column(db.Integer, default=40)
    gender = db.Column(db.String(20), default='ذكر')
    weight = db.Column(db.Float, default=70.0)
    height = db.Column(db.Float, default=170.0)
    bmi = db.Column(db.Float, default=24.2)
    blood = db.Column(db.String(10), default='O+')
    emergency_phone = db.Column(db.String(30), default='07700000000')
    diabetes_type = db.Column(db.String(50), default='النوع الثاني')
    doctor_notes = db.Column(db.Text, default='يرجى الالتزام بالعلاج والمتابعة المستمرة.')
    conditions = db.Column(db.String(255), default='')

    logs = db.relationship('GlucoseLog', backref='patient', lazy=True, cascade="all, delete-orphan")
    messages = db.relationship('ChatMessage', backref='patient', lazy=True, cascade="all, delete-orphan")

class GlucoseLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    val = db.Column(db.Integer, nullable=False)
    timing = db.Column(db.String(50), nullable=False)
    meds = db.Column(db.String(100), default='-')
    created_at = db.Column(db.DateTime, default=datetime.now)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sender = db.Column(db.String(20), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

def _user_dict(user):
    return {
        'id': user.id,
        'name': user.name,
        'username': user.username,
        'role': user.role,
        'age': user.age,
        'gender': user.gender,
        'weight': user.weight,
        'height': user.height,
        'bmi': user.bmi,
        'blood': user.blood,
        'emergencyPhone': user.emergency_phone,
        'diabetesType': user.diabetes_type,
        'doctorNotes': user.doctor_notes,
        'conditions': user.conditions.split(',') if user.conditions else []
    }

# ---------------- تهيئة قاعدة البيانات بأمان ----------------
def init_db():
    for _ in range(3):
        try:
            db.create_all()
            if not User.query.filter_by(username='doctor').first():
                doc = User(
                    username='doctor',
                    password_hash=generate_password_hash('123', method='pbkdf2:sha256'),
                    role='doctor',
                    name='د. خالد السامرائي'
                )
                db.session.add(doc)
                db.session.commit()

            patients_data = [
                {
                    'username': 'patient',
                    'password': '123',
                    'name': 'أحمد علي',
                    'age': 42,
                    'gender': 'ذكر',
                    'weight': 75.0,
                    'height': 175.0,
                    'bmi': 24.5,
                    'blood': 'O+',
                    'emergency_phone': '07701112233',
                    'diabetes_type': 'النوع الثاني',
                    'doctor_notes': 'الالتزام بالمشي نص ساعة يومياً وتقليل الخبز والتمن.',
                    'conditions': 'ضغط الدم',
                    'logs': [(115, 'على الريك', '4 وحدات'), (140, 'ورا الأكل', '-'), (122, 'عشوائي', '-')]
                },
                {
                    'username': 'patient2',
                    'password': '123',
                    'name': 'سارة فاضل',
                    'age': 31,
                    'gender': 'أنثى',
                    'weight': 64.0,
                    'height': 162.0,
                    'bmi': 24.4,
                    'blood': 'A+',
                    'emergency_phone': '07809998877',
                    'diabetes_type': 'سكري الحمل',
                    'doctor_notes': 'قيسي السكر ورا كل وجبة بساعة وسجليها بالجدول.',
                    'conditions': 'حمل',
                    'logs': [(95, 'على الريك', '-'), (132, 'ورا الأكل', '-')]
                },
                {
                    'username': 'patient3',
                    'password': '123',
                    'name': 'حسين جاسم',
                    'age': 58,
                    'gender': 'ذكر',
                    'weight': 88.0,
                    'height': 170.0,
                    'bmi': 30.4,
                    'blood': 'B+',
                    'emergency_phone': '07714445566',
                    'diabetes_type': 'النوع الثاني',
                    'doctor_notes': 'استمر على علاج الضغط وافحص رجليك يومياً.',
                    'conditions': 'اعتلال أعصاب، ضغط',
                    'logs': [(185, 'على الريك', 'منظم سكر'), (210, 'ورا الأكل', '-'), (165, 'عشوائي', '-')]
                },
                {
                    'username': 'patient4',
                    'password': '123',
                    'name': 'مريم كمال',
                    'age': 24,
                    'gender': 'أنثى',
                    'weight': 55.0,
                    'height': 165.0,
                    'bmi': 20.2,
                    'blood': 'O-',
                    'emergency_phone': '07503332211',
                    'diabetes_type': 'النوع الأول',
                    'doctor_notes': 'عدلي جرعة الأنسولين السريع حسب كمية الكارب بالأكل.',
                    'conditions': 'لا يوجد',
                    'logs': [(105, 'على الريك', '6 وحدات سريع'), (148, 'ورا الأكل', '4 وحدات')]
                },
                {
                    'username': 'patient5',
                    'password': '123',
                    'name': 'حيدر عبد الله',
                    'age': 50,
                    'gender': 'ذكر',
                    'weight': 82.0,
                    'height': 173.0,
                    'bmi': 27.4,
                    'blood': 'AB+',
                    'emergency_phone': '07726667788',
                    'diabetes_type': 'النوع الثاني',
                    'doctor_notes': 'قلل الدهون والأكل الدسم وتابع فحوصات القلب.',
                    'conditions': 'أمراض القلب',
                    'logs': [(130, 'على الريك', '-'), (160, 'ورا الأكل', 'منظم سكر')]
                }
            ]

            for item in patients_data:
                if not User.query.filter_by(username=item['username']).first():
                    p = User(
                        username=item['username'],
                        password_hash=generate_password_hash(item['password'], method='pbkdf2:sha256'),
                        role='patient',
                        name=item['name'],
                        age=item['age'],
                        gender=item['gender'],
                        weight=item['weight'],
                        height=item['height'],
                        bmi=item['bmi'],
                        blood=item['blood'],
                        emergency_phone=item['emergency_phone'],
                        diabetes_type=item['diabetes_type'],
                        doctor_notes=item['doctor_notes'],
                        conditions=item['conditions']
                    )
                    db.session.add(p)
                    db.session.commit()
                    for val, timing, meds in item['logs']:
                        db.session.add(GlucoseLog(user_id=p.id, val=val, timing=timing, meds=meds))
                    db.session.add(ChatMessage(patient_id=p.id, sender='patient', text='هلو دكتور، سجلت قياساتي مال اليوم.'))
                    db.session.add(ChatMessage(patient_id=p.id, sender='doctor', text='هلا بيك عيني، قياساتك كلش زينة استمر هيج.'))
                    db.session.commit()
            return
        except (IntegrityError, OperationalError):
            db.session.rollback()
            time.sleep(0.5)

@app.before_request
def ensure_db_initialized():
    global _db_ready
    if not _db_ready:
        init_db()
        _db_ready = True

# ---------------- مسارات النظام (Routes) ----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip().lower()
    password = str(data.get('password', '')).strip()
    
    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password_hash, password):
        session['user_id'] = user.id
        session['role'] = user.role
        return jsonify({'success': True, 'user': _user_dict(user)})
    return jsonify({'success': False, 'message': 'اليوزر أو الرمز السري غلط، تأكد منهم وحاول مرة ثانية'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me', methods=['GET'])
def get_current_user():
    if not session.get('user_id'):
        return jsonify({'logged_in': False})
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return jsonify({'logged_in': False})
    return jsonify({'logged_in': True, 'user': _user_dict(user)})

@app.route('/api/patient/update-profile', methods=['POST'])
def update_patient_profile():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'ما مسموحلك تفوت هنا'}), 401
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'الحساب ما موجود'}), 404

    data = request.get_json(silent=True) or {}
    if data.get('name'):
        user.name = data['name']
    if data.get('age'):
        user.age = int(data['age'])
    if data.get('weight'):
        user.weight = float(data['weight'])
    if data.get('height'):
        user.height = float(data['height'])
    if data.get('emergencyPhone'):
        user.emergency_phone = data['emergencyPhone']
    
    if user.height > 0:
        h_meters = user.height / 100.0
        user.bmi = round(user.weight / (h_meters * h_meters), 1)

    new_password = str(data.get('newPassword', '')).strip()
    if new_password:
        user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')

    db.session.commit()
    return jsonify({'success': True, 'message': 'تم تحديث بياناتك بنجاح', 'user': _user_dict(user)})

@app.route('/api/logs', methods=['GET', 'POST'])
def handle_logs():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مصرح'}), 401
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        db.session.add(GlucoseLog(user_id=user_id, val=int(data['val']), timing=data['timing'], meds=data.get('meds', '-')))
        db.session.commit()
        return jsonify({'success': True})
    logs = GlucoseLog.query.filter_by(user_id=user_id).order_by(GlucoseLog.created_at.desc()).all()
    return jsonify([{'id': l.id, 'val': l.val, 'timing': l.timing, 'meds': l.meds, 'date': l.created_at.strftime('%Y-%m-%d %H:%M')} for l in logs])

@app.route('/api/doctor/patients', methods=['GET'])
def get_doctor_patients():
    if session.get('role') != 'doctor':
        return jsonify({'error': 'غير مصرح'}), 403
    patients = User.query.filter_by(role='patient').all()
    result = []
    for p in patients:
        logs = GlucoseLog.query.filter_by(user_id=p.id).order_by(GlucoseLog.created_at.desc()).all()
        p_dict = _user_dict(p)
        p_dict['logs'] = [{'val': l.val, 'timing': l.timing, 'date': l.created_at.strftime('%Y-%m-%d %H:%M'), 'meds': l.meds} for l in logs]
        result.append(p_dict)
    return jsonify(result)

@app.route('/api/doctor/create-patient', methods=['POST'])
def create_patient():
    if session.get('role') != 'doctor':
        return jsonify({'error': 'غير مصرح'}), 403
    data = request.get_json(silent=True) or {}
    clean_username = str(data.get('username', '')).strip().lower()
    if User.query.filter_by(username=clean_username).first():
        return jsonify({'success': False, 'message': 'هذا اليوزر مستخدم كبل، اختار غيره'}), 400
    w = float(data.get('weight', 70))
    h = float(data.get('height', 170)) / 100.0
    bmi = round(w / (h * h), 1) if h > 0 else 24.2
    new_patient = User(
        username=clean_username,
        password_hash=generate_password_hash(str(data['password']).strip(), method='pbkdf2:sha256'),
        role='patient',
        name=data['name'],
        age=int(data.get('age', 40)),
        gender=data.get('gender', 'ذكر'),
        weight=w,
        height=float(data.get('height', 170)),
        bmi=bmi,
        blood=data.get('blood', 'O+'),
        emergency_phone=data.get('emergencyPhone', '07700000000'),
        diabetes_type=data.get('diabetesType', 'النوع الثاني')
    )
    db.session.add(new_patient)
    db.session.commit()
    return jsonify({'success': True, 'patient_id': new_patient.id})

@app.route('/api/doctor/update-notes', methods=['POST'])
def update_doctor_notes():
    if session.get('role') != 'doctor':
        return jsonify({'error': 'غير مصرح'}), 403
    data = request.get_json(silent=True) or {}
    patient = User.query.get(data.get('patient_id'))
    if patient:
        patient.doctor_notes = data.get('notes', '')
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'error': 'المراجع ما موجود'}), 404

@app.route('/api/chat/<int:patient_id>', methods=['GET', 'POST'])
def handle_chat(patient_id):
    role = session.get('role')
    uid = session.get('user_id')
    if role != 'doctor' and uid != patient_id:
        return jsonify({'error': 'غير مصرح'}), 403
    
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        db.session.add(ChatMessage(patient_id=patient_id, sender=role, text=data.get('text', '')))
        db.session.commit()
        return jsonify({'success': True})
    
    msgs = ChatMessage.query.filter_by(patient_id=patient_id).order_by(ChatMessage.created_at).all()
    return jsonify([{'id': m.id, 'sender': m.sender, 'text': m.text, 'time': m.created_at.strftime('%H:%M')} for m in msgs])

# ---------------- المساعد الطبي الذكي المربوط بـ Gemini (للمريض فقط وبمجال السكري حصراً) ----------------
@app.route('/api/ai/assistant', methods=['POST'])
def ai_assistant():
    if session.get('role') != 'patient' or not session.get('user_id'):
        return jsonify({'answer': 'عذراً، هذه الخدمة مخصصة فقط لحسابات المرضى المسجلين.'}), 403

    if not HAS_GENAI:
        return jsonify({'answer': f'⚠️ مكتبة google-genai غير مثبتة بالسيرفر: {GENAI_IMPORT_ERROR}'})

    data = request.get_json(silent=True) or {}
    question = str(data.get('question', '')).strip()
    if not question:
        return jsonify({'answer': 'اكتب سؤالك عن مرض السكري عيني حتى أجاوبك.'})

    user = User.query.get(session['user_id'])
    latest_log = GlucoseLog.query.filter_by(user_id=user.id).order_by(GlucoseLog.created_at.desc()).first() if user else None

    patient_context = ""
    if user:
        patient_context = f"بيانات المريض: الاسم {user.name}، العمر {user.age} سنة، نوع السكري: {user.diabetes_type}، مؤشر كتلة الجسم: {user.bmi}."
        if latest_log:
            patient_context += f" آخر قياس سكر مسجل: {latest_log.val} mg/dL ({latest_log.timing})."

    api_key = os.environ.get('GEMINI_API_KEY', '').strip().strip('"').strip("'")
    if not api_key:
        return jsonify({'answer': '⚠️ خدمة الذكاء الاصطناعي (Gemini) غير مفعلة حالياً. يرجى إضافة مفتاح GEMINI_API_KEY في إعدادات السيرفر (Variables).'})

    prompt_text = patient_context + "\n\nسؤال المريض: " + question
    errors = []

    for api_ver in ['v1beta', None]:
        try:
            if api_ver:
                client = genai.Client(api_key=api_key, http_options=types.HttpOptions(api_version=api_ver))
            else:
                client = genai.Client(api_key=api_key)

            candidate_models = ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash']
            try:
                for m in client.models.list():
                    m_name = getattr(m, 'name', '')
                    if m_name.startswith('models/'):
                        m_name = m_name.replace('models/', '', 1)
                    if 'gemini' in m_name.lower() and not any(x in m_name.lower() for x in ['embedding', 'imagen', 'veo', 'aqa', 'tts', 'audio']):
                        if m_name not in candidate_models:
                            if 'flash' in m_name.lower():
                                candidate_models.insert(0, m_name)
                            else:
                                candidate_models.append(m_name)
            except Exception as list_err:
                errors.append(f"list({api_ver}): {list_err}")

            for model_name in candidate_models[:6]:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt_text,
                        config=types.GenerateContentConfig(
                            system_instruction=DIABETES_SYSTEM_PROMPT,
                            temperature=0.3,
                        )
                    )
                    if response and response.text:
                        return jsonify({'answer': response.text.strip()})
                except Exception as e:
                    errors.append(f"{model_name}({api_ver}): {e}")
                    continue
        except Exception as client_err:
            errors.append(f"client({api_ver}): {client_err}")

    first_err = errors[0] if errors else "غير معروف"
    return jsonify({'answer': f'⚠️ تعذر الاتصال بجمناي. السبب الأول: {first_err}'})

@app.route('/api/ai/meal-analyze', methods=['POST'])
def analyze_meal():
    if 'image' not in request.files:
        return jsonify({'error': 'ماكو صورة مرفوعة'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'ما اختاريت أي ملف'}), 400
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    time.sleep(1.2)
    
    return jsonify({
        'success': True,
        'meal_name': 'وجبة غداء رئيسية (تمن، بروتين، زلاطة)',
        'carbs': '55 - 65 غرام كربوهيدرات',
        'calories': '430 سعرة حرارية',
        'insulin_dose': '4 وحدات أنسولين سريع (حسب معامل الكارب 1:15)',
        'tips': 'نصيحة: ابدي بالزلاطة والخضروات كبل التمن حتى ما يصعد السكر عندك بسرعة ورا الأكل.'
    })

if __name__ == '__main__':
    with app.app_context():
        init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
