from flask import Flask, render_template, request, redirect, session, send_file, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_mysqldb import MySQL
from MySQLdb.cursors import DictCursor
import qrcode
import os
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import requests
from flask import flash
from datetime import datetime
from io import BytesIO

app = Flask(__name__)
app.secret_key = "secure_organ_flow_key_2026"  # In production, use a random string

app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'harshilp@18'
app.config['MYSQL_DB'] = 'organ_donation'
app.config['MYSQL_PORT'] = 3308
mysql = MySQL(app)

with app.app_context():
    cur = mysql.connection.cursor(DictCursor)

    cur.execute("SELECT * FROM users WHERE role=%s", ("admin",))
    admin = cur.fetchone()

    if not admin:
        hashed_pw = generate_password_hash("admin123")

        cur.execute(
            "INSERT INTO users(name, email, hashed_password, role) VALUES(%s,%s,%s,%s)",
            ("System Administrator", "admin@gmail.com", hashed_pw, "admin")
        )

        mysql.connection.commit()

    cur.close()


# ---------------- AUTHENTICATION DECORATOR LOGIC ---------------- #

def is_logged_in():
    return 'user_id' in session

def is_admin():
    return session.get('role') == 'admin'

# ---------------- ROUTES ---------------- #


@app.route('/')
def index():
    return render_template("index.html")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        role = request.form.get('role', 'donor')

        try:
            cur = mysql.connection.cursor(DictCursor)

            cur.execute(
                "INSERT INTO users(name, email, hashed_password, role) VALUES(%s,%s,%s,%s)",
                (name, email, password, role)
            )

            mysql.connection.commit()
            cur.close()

            flash("Registration Successful! Please login.", "success")
            return redirect(url_for('login'))

        except Exception as e:
            flash("Email already registered!", "error")
            return redirect(url_for('register'))

    return render_template("register.html")

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        cur = mysql.connection.cursor(DictCursor)

        cur.execute(
            "SELECT * FROM users WHERE email=%s",
            (email,)
        )

        user = cur.fetchone()
        cur.close()

        if user and check_password_hash(user['hashed_password'], password):

            session.clear()
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['role'] = user['role']

            flash("Login Successful!", "success")

            if user['role'] == 'admin':
                return redirect(url_for('admin'))
            elif user['role'] == 'donor':
                return redirect(url_for('donor'))
            else:
                return redirect(url_for('recipient'))

        else:
            flash("Invalid email or password!", "error")
            return redirect(url_for('login'))

    return render_template("login.html")

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():

    if request.method == 'POST':
        email = request.form['email']
        new_password = generate_password_hash(request.form['new_password'])

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

        if user:
            conn.execute(
                "UPDATE users SET password=? WHERE email=?",
                (new_password, email)
            )
            conn.commit()
            flash("Password updated successfully!", "success")
            return redirect(url_for('login'))
        else:
            flash("Email not found!", "error")
            return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')

@app.route('/donor', methods=['GET', 'POST'])
def donor():
    print("SESSION:", session)

    if not is_logged_in() or session.get('role') != 'donor':
        return redirect(url_for('login'))

    cur = mysql.connection.cursor(DictCursor)

    if request.method == 'POST':
        print("POST BLOCK RUNNING")

        full_name = request.form['full_name']
        age = request.form['age']
        blood_group = request.form['blood_group']
        organ = request.form['organ']
        phone = request.form['phone']
        address = request.form['address']
        full_address = request.form['full_address']
        medical_history = request.form.get('medical_history', '')

        files = request.files.getlist('documents')
        filenames = []

        upload_folder = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)

        for file in files:
            if file and file.filename != "":
                filename = secure_filename(file.filename)
                file.save(os.path.join(upload_folder, filename))
                filenames.append(filename)

        documents_string = ",".join(filenames)

        # ===== INSERT INTO DATABASE =====
        cur.execute('''
            INSERT INTO donations
            (user_id, full_name, age, blood_group, organ, phone, address, full_address, medical_history, documents)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            session['user_id'], full_name, age, blood_group, organ,
            phone, address, full_address, medical_history, documents_string
        ))

        mysql.connection.commit()

    # ===== FETCH DONATIONS =====
    cur.execute(
        "SELECT * FROM donations WHERE user_id=%s",
        (session['user_id'],)
    )
    donations = cur.fetchall()

    # ===== FETCH TRANSPLANTS =====
    cur.execute("""
        SELECT tr.*, rr.full_name AS recipient_name, rr.requested_organ
        FROM transplant_records tr
        JOIN donations d ON tr.donor_id = d.id
        JOIN recipient_requests rr ON tr.recipient_id = rr.id
        WHERE d.user_id = %s
        ORDER BY tr.created_at DESC
    """, (session['user_id'],))

    transplants = cur.fetchall()

    cur.close()

    return render_template(
        "donor.html",
        donations=donations,
        transplants=transplants
    )
    
@app.route('/recipient', methods=['GET', 'POST'])
def recipient():

    if 'user_id' not in session or session.get('role') != 'recipient':
        return redirect(url_for('login'))

    cur = mysql.connection.cursor(DictCursor)
    matched_donors = []

    if request.method == 'POST':
        action = request.form.get('action')

        # ===== SUBMIT REQUEST =====
        if action == "submit_request":
            full_name = request.form.get('full_name', '').strip()
            age = request.form.get('age')
            blood_group = request.form.get('blood_group', '').strip()
            requested_organ = request.form.get('requested_organ', '').strip()
            urgency = request.form.get('urgency')
            phone = request.form.get('phone', '').strip()
            address = request.form.get('address', '').strip()

            # Phone validation
            if not phone.isdigit() or len(phone) != 10:
                flash("Contact number must be exactly 10 digits.", "error")
                return redirect(url_for('recipient'))

            cur.execute("""
                INSERT INTO recipient_requests
                (user_id, full_name, age, blood_group, requested_organ, phone, address, urgency)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                session['user_id'],
                full_name,
                age,
                blood_group,
                requested_organ,
                phone,
                address,
                urgency
            ))

            mysql.connection.commit()

            flash("Request submitted successfully!", "success")
            cur.close()
            return redirect(url_for('recipient'))

        # ===== SEARCH MATCH =====
        elif action == "search_match":

            cur.execute("""
                SELECT * FROM recipient_requests
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (session['user_id'],))

            latest_request = cur.fetchone()

            if not latest_request:
                flash("Please submit a request first!", "error")
                cur.close()
                return redirect(url_for('recipient'))

            cur.execute("""
                SELECT * FROM donations
                WHERE LOWER(TRIM(blood_group)) = LOWER(TRIM(%s))
                  AND LOWER(TRIM(organ)) = LOWER(TRIM(%s))
                  AND status = 'Approved'
            """, (
                latest_request['blood_group'],
                latest_request['requested_organ']
            ))

            matched_donors = cur.fetchall()

            if not matched_donors:
                flash("No matching donors found yet.", "error")
            else:
                flash(f"{len(matched_donors)} donor(s) found!", "success")

    # ===== FETCH ALL REQUESTS =====
    cur.execute("""
        SELECT * FROM recipient_requests
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (session['user_id'],))

    recipient_requests = cur.fetchall()

    # ===== ASSIGNED TRANSPLANTS =====
    cur.execute("""
        SELECT tr.*, tr.organ AS donor_organ, d.full_name AS donor_name
        FROM transplant_records tr
        JOIN recipient_requests rr ON tr.recipient_id = rr.id
        JOIN donations d ON tr.donor_id = d.id
        WHERE rr.user_id = %s
        ORDER BY tr.created_at DESC
    """, (session['user_id'],))

    assigned_transplants = cur.fetchall()

    cur.close()

    return render_template(
        'recipient.html',
        recipient_requests=recipient_requests,
        matched_donors=matched_donors,
        assigned_transplants=assigned_transplants
    )
        
@app.route('/create_transplant', methods=['POST'])
def create_transplant():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    cur = mysql.connection.cursor(DictCursor)

    donor_id = request.form['donor_id']
    recipient_id = request.form['recipient_id']
    hospital = request.form['hospital']
    transplant_date = request.form['transplant_date']

    # 🔹 Fetch donor
    cur.execute("""
        SELECT organ, blood_group
        FROM donations
        WHERE id = %s AND status = 'Approved'
    """, (donor_id,))
    donor = cur.fetchone()

    # 🔹 Fetch recipient
    cur.execute("""
        SELECT requested_organ, blood_group
        FROM recipient_requests
        WHERE id = %s
    """, (recipient_id,))
    recipient = cur.fetchone()

    # 🚨 Safety check
    if not donor or not recipient:
        cur.close()
        flash("Invalid donor or recipient selection!", "error")
        return redirect(url_for('admin'))

    # 🔥 VALIDATION 1 — Organ Match
    if donor['organ'].strip().lower() != recipient['requested_organ'].strip().lower():
        cur.close()
        flash("Organ mismatch! Donor and recipient organs do not match.", "error")
        return redirect(url_for('admin'))

    # 🔥 VALIDATION 2 — Blood Group Match
    if donor['blood_group'].strip().lower() != recipient['blood_group'].strip().lower():
        cur.close()
        flash("Blood group mismatch! Transplant not allowed.", "error")
        return redirect(url_for('admin'))

    # ✅ Insert transplant
    cur.execute("""
        INSERT INTO transplant_records
        (donor_id, recipient_id, organ, hospital, transplant_date)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        donor_id,
        recipient_id,
        donor['organ'],
        hospital,
        transplant_date
    ))

    mysql.connection.commit()
    cur.close()

    flash("Transplant scheduled successfully!", "success")
    return redirect(url_for('admin'))  
 

@app.route('/admin')
def admin():
    if not is_admin():
        return redirect(url_for('login'))

    cur = mysql.connection.cursor(DictCursor)

    # ===== FETCH DONATIONS =====
    cur.execute("""
        SELECT id, user_id, full_name, age, blood_group, organ, phone,
               address, full_address, medical_history, documents,
               status, certificate, created_at
        FROM donations
        ORDER BY created_at DESC
    """)
    donations = cur.fetchall()

    # ===== FETCH RECIPIENTS =====
    cur.execute("""
        SELECT id, user_id, full_name, age, blood_group,
               requested_organ, phone, address, urgency, created_at
        FROM recipient_requests
        ORDER BY created_at DESC
    """)
    recipients = cur.fetchall()
    
    # ===== CREATE ELIGIBLE PAIRS =====
    eligible_pairs = []
    for recipient in recipients:
        for donor in donations:
            if (donor['status'] == 'Approved' and
                donor['organ'].strip().lower() == recipient['requested_organ'].strip().lower() and
                donor['blood_group'].strip().lower() == recipient['blood_group'].strip().lower()):
                eligible_pairs.append({
                    'donor': donor,
                    'recipient': recipient
                })

    # ===== FETCH TRANSPLANTS =====
    cur.execute("""
        SELECT tr.id,
               d.full_name AS donor_name,
               d.organ AS donor_organ,
               rr.full_name AS recipient_name,
               tr.hospital,
               tr.transplant_date,
               tr.status,
               tr.created_at
        FROM transplant_records tr
        JOIN donations d ON tr.donor_id = d.id
        JOIN recipient_requests rr ON tr.recipient_id = rr.id
        ORDER BY tr.created_at DESC
    """)
    transplants = cur.fetchall()

    # ===== FETCH USERS =====
    cur.execute("""
        SELECT id, name, email, role
        FROM users
        ORDER BY id DESC
    """)
    users = cur.fetchall()

    cur.close()

    # 🔹 RETURN MUST BE INSIDE FUNCTION
    return render_template(
        "admin.html",
        donations=donations,
        recipients=recipients,
        pairs=eligible_pairs,
        transplants=transplants,
        users=users
    ) 
    

@app.route('/approve/<int:id>')
def approve(id):
    if not is_admin():
        return redirect(url_for('login'))

    cur = mysql.connection.cursor(DictCursor)

    # Fetch donation
    cur.execute("SELECT * FROM donations WHERE id=%s", (id,))
    donation = cur.fetchone()

    if not donation:
        cur.close()
        return "Donation record not found"

    cert_dir = "static/certificates"
    if not os.path.exists(cert_dir):
        os.makedirs(cert_dir)

    filename = f"{cert_dir}/certificate_{id}.pdf"

    # -------- YOUR PDF CODE SAME RAHEGA --------
    c = canvas.Canvas(filename, pagesize=landscape(letter))
    width, height = landscape(letter)

    # (PDF design code unchanged...)

    c.save()

    # Update donation status
    cur.execute(
        "UPDATE donations SET status=%s, certificate=%s WHERE id=%s",
        ("Approved", filename, id)
    )
    mysql.connection.commit()

    cur.close()

    return redirect(url_for('admin'))


@app.route('/download/<int:id>')
def download(id):
    cur = mysql.connection.cursor(DictCursor)

    cur.execute("SELECT * FROM donations WHERE id=%s", (id,))
    donation = cur.fetchone()
    cur.close()

    if donation and donation['certificate']:
        # Security: only allow admin or the owner
        if is_admin() or session.get('user_id') == donation['user_id']:
            if os.path.exists(donation['certificate']):
                return send_file(donation['certificate'], as_attachment=True)

    return "Unauthorized or file not found.", 403


from MySQLdb.cursors import DictCursor

@app.route('/reject/<int:id>')
def reject(id):
    if not is_admin():
        return redirect(url_for('login'))

    cur = mysql.connection.cursor(DictCursor)

    cur.execute(
        "UPDATE donations SET status=%s WHERE id=%s",
        ("Rejected", id)
    )

    mysql.connection.commit()
    cur.close()

    return redirect(url_for('admin'))


@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully!", "success")
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)