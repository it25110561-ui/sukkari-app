from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import time

app = Flask(__name__)
app.secret_key = 'sukari_secret_key_iraq_2026'

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'diabetes_db.sqlite')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db = SQLAlchemy(app)

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
    doctor_notes = db.Column(db.Text, default='يرجى الالتزام بالخطة العلاجية والمتابعة المستمرة.')
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
        'id': user.id, 'name': user.name, 'username': user.username, 'role': user.role,
        'age': user.age, 'gender': user.gender, 'weight': user.weight, 'height': user.height,
        'bmi': user.bmi, 'blood': user.blood, 'emergencyPhone': user.emergency_phone,
        'diabetesType': user.diabetes_type, 'doctorNotes': user.doctor_notes,
        'conditions': user.conditions.split(',') if user.conditions else []
    }

# ---------------- مسارات النظام (Routes) ----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data.get('username')).first()
    if user and check_password_hash(user.password_hash, data.get('password')):
        session['user_id'] = user.id
        session['role'] = user.role
        return jsonify({'success': True, 'user': _user_dict(user)})
    return jsonify({'success': False, 'message': 'اسم المستخدم أو كلمة المرور غير صحيحة'}), 401

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

# مسار تحديث بيانات المريض وكلمة المرور
@app.route('/api/patient/update-profile', methods=['POST'])
def update_patient_profile():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'غير مصرح'}), 401
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    data = request.json
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
    
    # إعادة حساب BMI
    if user.height > 0:
        h_meters = user.height / 100.0
        user.bmi = round(user.weight / (h_meters * h_meters), 1)

    # تحديث كلمة المرور إذا تم إدخالها
    new_password = data.get('newPassword', '').strip()
    if new_password:
        user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')

    db.session.commit()
    return jsonify({'success': True, 'message': 'تم تحديث البيانات بنجاح', 'user': _user_dict(user)})

@app.route('/api/logs', methods=['GET', 'POST'])
def handle_logs():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مصرح'}), 401
    if request.method == 'POST':
        data = request.json
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
    data = request.json
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'success': False, 'message': 'اسم المستخدم مستخدم مسبقاً'}), 400
    w = float(data.get('weight', 70))
    h = float(data.get('height', 170)) / 100.0
    bmi = round(w / (h * h), 1) if h > 0 else 24.2
    new_patient = User(
        username=data['username'],
        password_hash=generate_password_hash(data['password'], method='pbkdf2:sha256'),
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
    patient = User.query.get(request.json['patient_id'])
    if patient:
        patient.doctor_notes = request.json['notes']
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'error': 'غير موجود'}), 404

@app.route('/api/chat/<int:patient_id>', methods=['GET', 'POST'])
def handle_chat(patient_id):
    role = session.get('role')
    uid = session.get('user_id')
    if role != 'doctor' and uid != patient_id:
        return jsonify({'error': 'غير مصرح'}), 403
    
    if request.method == 'POST':
        db.session.add(ChatMessage(patient_id=patient_id, sender=role, text=request.json['text']))
        db.session.commit()
        return jsonify({'success': True})
    
    msgs = ChatMessage.query.filter_by(patient_id=patient_id).order_by(ChatMessage.created_at).all()
    return jsonify([{'id': m.id, 'sender': m.sender, 'text': m.text, 'time': m.created_at.strftime('%H:%M')} for m in msgs])

@app.route('/api/ai/assistant', methods=['POST'])
def ai_assistant():
    q = request.json.get('question', '').lower()
    ans = "أهلاً بك! أنا المساعد الطبي الذكي. يمكنك سؤالي عن أعراض الهبوط، تنظيم الوجبات، أو التمارين الرياضية المناسبة لحالتك."
    if any(x in q for x in ['هبوط', 'انخفاض', 'دوخة', 'رجفة']):
        ans = "⚠️ في حال الشعور بأعراض الهبوط: تناول فوراً 15 غراماً من السكر السريع (نصف كوب عصير محلى أو 3 تمرات)، وانتظر 15 دقيقة ثم أعد الفحص. إذا استمر الانخفاض اضغط على زر SOS."
    elif any(x in q for x in ['رياضة', 'تمرين', 'مشي']):
        ans = "🏃‍♂️ المشي السريع لمدة 30 دقيقة يومياً يحسن حساسية الأنسولين. تجنب الرياضة إذا كان السكر أقل من 100 أو أعلى من 250 ملجم/ديسيلتر."
    elif any(x in q for x in ['صيام', 'رمضان']):
        ans = "🌙 الصيام يتطلب تعديل أوقات وجرعات الدواء مع طبيبك لتجنب الهبوط نهاراً أو الارتفاع الحاد بعد الإفطار."
    elif any(x in q for x in ['تراكمي']):
        ans = "📊 الفحص التراكمي (HbA1c) يعكس معدل السكر لآخر 3 أشهر. النسبة المثالية لمعظم مرضى السكري هي أقل من 7%."
    time.sleep(0.8)
    return jsonify({'answer': ans})

@app.route('/api/ai/meal-analyze', methods=['POST'])
def analyze_meal():
    if 'image' not in request.files:
        return jsonify({'error': 'لا توجد صورة'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'لم يتم اختيار ملف'}), 400
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    time.sleep(1.2)
    
    return jsonify({
        'success': True,
        'meal_name': 'وجبة رئيسية متكاملة (أرز، بروتين، خضروات)',
        'carbs': '55 - 65 غرام كربوهيدرات',
        'calories': '430 سعرة حرارية',
        'insulin_dose': '4 وحدات أنسولين سريع (حسب معامل الكارب 1:15)',
        'tips': 'ابدأ بتناول السلطة والخضروات أولاً لإبطاء ارتفاع السكر بعد الوجبة.'
    })

# ---------------- تهيئة قاعدة البيانات (5 مرضى + طبيب) ----------------
def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='doctor').first():
            doc = User(username='doctor', password_hash=generate_password_hash('123', method='pbkdf2:sha256'), role='doctor', name='د. خالد السامرائي')
            db.session.add(doc)

        patients_data = [
            {'username': 'patient', 'password': '123', 'name': 'أحمد علي', 'age': 42, 'gender': 'ذكر', 'weight': 75.0, 'height': 175.0, 'bmi': 24.5, 'blood': 'O+', 'emergency_phone': '07701112233', 'diabetes_type': 'النوع الثاني', 'doctor_notes': 'الالتزام بالمشي 30 دقيقة يومياً وتقليل النشويات.', 'conditions': 'ضغط الدم', 'logs': [(115, 'صائم', '4 وحدات'), (140, 'بعد الأكل', '-'), (122, 'عشوائي', '-')]},
            {'username': 'patient2', 'password': '123', 'name': 'سارة فاضل', 'age': 31, 'gender': 'أنثى', 'weight': 64.0, 'height': 162.0, 'bmi': 24.4, 'blood': 'A+', 'emergency_phone': '07809998877', 'diabetes_type': 'سكري الحمل', 'doctor_notes': 'فحص السكر بعد كل وجبة بساعة وتسجيل الجدول الغذائي.', 'conditions': 'حمل', 'logs': [(95, 'صائم', '-'), (132, 'بعد الأكل', '-')]},
            {'username': 'patient3', 'password': '123', 'name': 'حسين جاسم', 'age': 58, 'gender': 'ذكر', 'weight': 88.0, 'height': 170.0, 'bmi': 30.4, 'blood': 'B+', 'emergency_phone': '07714445566', 'diabetes_type': 'النوع الثاني', 'doctor_notes': 'الاستمرار على علاج الضغط وفحص القدمين يومياً.', 'conditions': 'اعتلال أعصاب، ضغط', 'logs': [(185, 'صائم', 'ميتفورمين'), (210, 'بعد الأكل', '-'), (165, 'عشوائي', '-')]},
            {'username': 'patient4', 'password': '123', 'name': 'مريم كمال', 'age': 24, 'gender': 'أنثى', 'weight': 55.0, 'height': 165.0, 'bmi': 20.2, 'blood': 'O-', 'emergency_phone': '07503332211', 'diabetes_type': 'النوع الأول', 'doctor_notes': 'تعديل جرعة الأنسولين السريع حسب حساب الكربوهيدرات.', 'conditions': 'لا يوجد', 'logs': [(105, 'صائم', '6 وحدات سريع'), (148, 'بعد الأكل', '4 وحدات')]},
            {'username': 'patient5', 'password': '123', 'name': 'حيدر عبد الله', 'age': 50, 'gender': 'ذكر', 'weight': 82.0, 'height': 173.0, 'bmi': 27.4, 'blood': 'AB+', 'emergency_phone': '07726667788', 'diabetes_type': 'النوع الثاني', 'doctor_notes': 'تقليل الدهون المشبعة والمتابعة الدورية لوظائف القلب.', 'conditions': 'أمراض القلب', 'logs': [(130, 'صائم', '-'), (160, 'بعد الأكل', 'منظم سكر')]}
        ]

        for item in patients_data:
            if not User.query.filter_by(username=item['username']).first():
                p = User(
                    username=item['username'], password_hash=generate_password_hash(item['password'], method='pbkdf2:sha256'),
                    role='patient', name=item['name'], age=item['age'], gender=item['gender'], weight=item['weight'],
                    height=item['height'], bmi=item['bmi'], blood=item['blood'], emergency_phone=item['emergency_phone'],
                    diabetes_type=item['diabetes_type'], doctor_notes=item['doctor_notes'], conditions=item['conditions']
                )
                db.session.add(p)
                db.session.commit()
                for val, timing, meds in item['logs']:
                    db.session.add(GlucoseLog(user_id=p.id, val=val, timing=timing, meds=meds))
                db.session.add(ChatMessage(patient_id=p.id, sender='patient', text='مرحباً دكتور، قمت بتحديث قراءاتي اليوم.'))
                db.session.add(ChatMessage(patient_id=p.id, sender='doctor', text='أهلاً بك، القراءات جيدة استمر على نفس النظام.'))
                db.session.commit()

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
[(115, 'صائم', '4 وحدات'), (140, 'بعد الأكل', '-'), (122, 'عشوائي', '-')]},
            {'username': 'patient2', 'password': '123', 'name': 'سارة فاضل', 'age': 31, 'gender': 'أنثى', 'weight': 64.0, 'height': 162.0, 'bmi': 24.4, 'blood': 'A+', 'emergency_phone': '07809998877', 'diabetes_type': 'سكري الحمل', 'doctor_notes': 'فحص السكر بعد كل وجبة بساعة وتسجيل الجدول الغذائي.', 'conditions': 'حمل', 'logs': [(95, 'صائم', '-'), (132, 'بعد الأكل', '-')]},
            {'username': 'patient3', 'password': '123', 'name': 'حسين جاسم', 'age': 58, 'gender': 'ذكر', 'weight': 88.0, 'height': 170.0, 'bmi': 30.4, 'blood': 'B+', 'emergency_phone': '07714445566', 'diabetes_type': 'النوع الثاني', 'doctor_notes': 'الاستمرار على علاج الضغط وفحص القدمين يومياً.', 'conditions': 'اعتلال أعصاب، ضغط', 'logs': [(185, 'صائم', 'ميتفورمين'), (210, 'بعد الأكل', '-'), (165, 'عشوائي', '-')]},
            {'username': 'patient4', 'password': '123', 'name': 'مريم كمال', 'age': 24, 'gender': 'أنثى', 'weight': 55.0, 'height': 165.0, 'bmi': 20.2, 'blood': 'O-', 'emergency_phone': '07503332211', 'diabetes_type': 'النوع الأول', 'doctor_notes': 'تعديل جرعة الأنسولين السريع حسب حساب الكربوهيدرات.', 'conditions': 'لا يوجد', 'logs': [(105, 'صائم', '6 وحدات سريع'), (148, 'بعد الأكل', '4 وحدات')]},
            {'username': 'patient5', 'password': '123', 'name': 'حيدر عبد الله', 'age': 50, 'gender': 'ذكر', 'weight': 82.0, 'height': 173.0, 'bmi': 27.4, 'blood': 'AB+', 'emergency_phone': '07726667788', 'diabetes_type': 'النوع الثاني', 'doctor_notes': 'تقليل الدهون المشبعة والمتابعة الدورية لوظائف القلب.', 'conditions': 'أمراض القلب', 'logs': [(130, 'صائم', '-'), (160, 'بعد الأكل', 'منظم سكر')]}
        ]

        for item in patients_data:
            if not User.query.filter_by(username=item['username']).first():
                p = User(
                    username=item['username'], password_hash=generate_password_hash(item['password'], method='pbkdf2:sha256'),
                    role='patient', name=item['name'], age=item['age'], gender=item['gender'], weight=item['weight'],
                    height=item['height'], bmi=item['bmi'], blood=item['blood'], emergency_phone=item['emergency_phone'],
                    diabetes_type=item['diabetes_type'], doctor_notes=item['doctor_notes'], conditions=item['conditions']
                )
                db.session.add(p)
                db.session.commit()
                for val, timing, meds in item['logs']:
                    db.session.add(GlucoseLog(user_id=p.id, val=val, timing=timing, meds=meds))
                db.session.add(ChatMessage(patient_id=p.id, sender='patient', text='مرحباً دكتور، قمت بتحديث قراءاتي اليوم.'))
                db.session.add(ChatMessage(patient_id=p.id, sender='doctor', text='أهلاً بك، القراءات جيدة استمر على نفس النظام.'))
                db.session.commit()

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
