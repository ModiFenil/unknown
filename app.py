from flask import Flask, render_template, request, redirect, session, url_for, make_response, flash
from werkzeug.utils import secure_filename
import pymysql
import hashlib
from config import config
from datetime import datetime, date, time
import os
import io
import csv
import calendar
from flask_apscheduler import APScheduler
import pytz
from datetime import timezone, timedelta

# DEFINE IST TIMEZONE
IST = pytz.timezone('Asia/Kolkata')

# CREATE IST HELPER FUNCTIONS
def get_ist_now():
    """Get current datetime in IST as string for database"""
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

def get_ist_date():
    """Get current date in IST"""
    return datetime.now(IST).date()

def get_ist_time_for_display():
    """Get current time in IST for display"""
    return datetime.now(IST).strftime("%I:%M %p")

# Initialize scheduler at module level
scheduler = APScheduler()

def create_app(config_name=None):
    app = Flask(__name__)
    # Get config name from environment or use default
    config_name = config_name or os.environ.get('FLASK_ENV', 'default')
    # Apply configuration
    app.config.from_object(config[config_name])
    # Initialize app with config
    config[config_name].init_app(app)
    
    # Configure scheduler timezone
    app.config['SCHEDULER_TIMEZONE'] = 'Asia/Kolkata'
    
    # Initialize scheduler with app
    scheduler.init_app(app)
    return app

app = create_app()

def get_db_connection():
    conn = pymysql.connect(
        host=app.config['MYSQL_HOST'],
        user=app.config['MYSQL_USER'],
        password=app.config['MYSQL_PASSWORD'],
        db=app.config['MYSQL_DB'],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )
    
    # Set database session to IST
    with conn.cursor() as cursor:
        cursor.execute("SET time_zone = '+05:30'")
    
    return conn

# SHA256 password hashing functions
def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def verify_password(password, hashed_password):
    """Verify password against SHA256 hash"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest() == hashed_password

@app.template_filter('datetimeformat')
def datetimeformat(value, format='%d-%b-%Y %I:%M %p'):
    if not value:
        return ''
    if isinstance(value, str):
        value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    return value.strftime(format)

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, password, firstname, lastname, role FROM t_users_hr WHERE email=%s AND is_active=1", (email,))
            user = cursor.fetchone()
            
            # SHA256 password verification
            if user and verify_password(password, user['password']):
                session['user_id'] = user['id']
                session['firstname'] = user['firstname']
                session['lastname'] = user['lastname']
                session['role'] = user['role']
                
                if user['role'] == 2:  # admin
                    return redirect('/admin_dashboard')
                else:
                    return redirect('/user_dashboard')
            else:
                error = 'Invalid email or password.'
        
        conn.close()
    
    return render_template('login.html', error=error)

@app.route('/test_time')
def test_time():
    try:
        ist_time = get_ist_now()
        ist_date = get_ist_date()
        server_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Calculate the difference
        ist_dt = datetime.now(IST)
        server_dt = datetime.now()
        diff_hours = (ist_dt - server_dt.replace(tzinfo=pytz.UTC)).total_seconds() / 3600
        
        return f"""
        <h2>Time Comparison</h2>
        <p><strong>IST Time:</strong> {ist_time}</p>
        <p><strong>IST Date:</strong> {ist_date}</p>
        <p><strong>Server Time:</strong> {server_time}</p>
        <p><strong>Time Difference:</strong> {diff_hours:.1f} hours</p>
        <p><strong>Status:</strong> {'✅ IST Working' if abs(diff_hours - 5.5) < 0.1 else '❌ IST Not Working'}</p>
        """
    except Exception as e:
        return f"<h2>Error</h2><p>{str(e)}</p><p>IST helper functions not working!</p>"

@app.route('/user_dashboard', methods=['GET', 'POST'])
def user_dashboard():
    if 'user_id' not in session or session.get('role') == 2:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    msg = None

    if request.method == 'POST':
        action = request.form.get('action')
        now = get_ist_now()  # Use IST time instead of datetime.now()
        
        # Get location data from form
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        location_name = request.form.get('location_name')

        # Convert to proper types
        check_in_latitude = float(latitude) if latitude else None
        check_in_longitude = float(longitude) if longitude else None

        with conn.cursor() as cursor:
            if action == 'checkin':
                # Check if already checked in today (using IST date)
                cursor.execute("SELECT id FROM t_attendance_hr WHERE user_id=%s AND DATE(checkin_time) = %s", 
                             (user_id, get_ist_date()))
                
                if cursor.fetchone():
                    msg = "You have already checked in today."
                else:
                    cursor.execute(
                        "INSERT INTO t_attendance_hr (user_id, checkin_time, check_in_latitude, check_in_longitude, check_in_location_name) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, now, check_in_latitude, check_in_longitude, location_name)
                    )
                    if location_name:
                        msg = f"Checked in successfully at {get_ist_time_for_display()} from {location_name}."
                    else:
                        msg = f"Checked in successfully at {get_ist_time_for_display()}."

            elif action == 'checkout':
                # Check for existing check-in today (using IST date)
                cursor.execute(
                    "SELECT id, checkin_time FROM t_attendance_hr WHERE user_id=%s AND DATE(checkin_time) = %s AND checkout_time IS NULL",
                    (user_id, get_ist_date())
                )
                
                attendance = cursor.fetchone()
                if attendance:
                    check_out_latitude = float(latitude) if latitude else None
                    check_out_longitude = float(longitude) if longitude else None

                    cursor.execute(
                        "UPDATE t_attendance_hr SET checkout_time=%s, work_duration=TIMEDIFF(%s, checkin_time), check_out_longitude=%s, check_out_latitude=%s, check_out_location_name=%s WHERE id=%s",
                        (now, now, check_out_longitude, check_out_latitude, location_name, attendance['id'])
                    )
                    if location_name:
                        msg = f"Checked out successfully at {get_ist_time_for_display()} from {location_name}."
                    else:
                        msg = f"Checked out successfully at {get_ist_time_for_display()}."
                else:
                    msg = "You must check in first or have already checked out."

    # Get user details
    with conn.cursor() as cursor:
        cursor.execute("SELECT firstname, lastname FROM t_users_hr WHERE id = %s", (user_id,))
        user = cursor.fetchone()

    # Get attendance history for the user (last 10 records)
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT 
            checkin_time, 
            checkout_time, 
            work_duration,
            check_in_location_name,
            check_out_location_name,
            auto_checkout
        FROM t_attendance_hr 
        WHERE user_id = %s 
        ORDER BY checkin_time DESC 
        LIMIT 10
        """, (user_id,))
        history = cursor.fetchall()

    conn.close()

    return render_template(
        'user_dashboard.html',
        firstname=user['firstname'] if user else '',
        lastname=user['lastname'] if user else '',
        history=history,
        msg=msg,
        current_year=get_ist_date().year
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin_dashboard')
def admin_dashboard():
    if 'user_id' not in session or session.get('role') != 2:
        return redirect(url_for('login'))

    conn = get_db_connection()
    
    # Get date filter from query parameters
    selected_date_str = request.args.get('date')
    status_filter = request.args.get('status')

    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = get_ist_date()  # Use IST date
    else:
        selected_date = get_ist_date()  # Use IST date

    try:
        with conn.cursor() as cursor:
            # Check if new columns exist, if not use old columns
            try:
                cursor.execute("SHOW COLUMNS FROM t_attendance_hr LIKE 'check_in_latitude'")
                new_columns_exist = cursor.fetchone() is not None
            except Exception as e:
                print(f"Column check error: {e}")
                new_columns_exist = False

            if new_columns_exist:
                # Use new column structure
                query = """
                SELECT
                    u.id as user_id,
                    u.firstname,
                    u.lastname,
                    a.checkin_time,
                    a.checkout_time,
                    a.work_duration,
                    a.check_in_latitude,
                    a.check_in_longitude,
                    a.check_in_location_name,
                    a.check_out_latitude,
                    a.check_out_longitude,
                    a.check_out_location_name,
                    a.auto_checkout
                FROM t_users_hr u
                LEFT JOIN t_attendance_hr a ON u.id = a.user_id
                    AND DATE(a.checkin_time) = %s
                WHERE u.role = 1 AND u.is_active = 1
                """
            else:
                # Use old column structure with aliases to match template expectations
                query = """
                SELECT
                    u.id as user_id,
                    u.firstname,
                    u.lastname,
                    a.checkin_time,
                    a.checkout_time,
                    a.work_duration,
                    a.latitude as check_in_latitude,
                    a.longitude as check_in_longitude,
                    a.location_name as check_in_location_name,
                    NULL as check_out_latitude,
                    NULL as check_out_longitude,
                    NULL as check_out_location_name,
                    a.auto_checkout
                FROM t_users_hr u
                LEFT JOIN t_attendance_hr a ON u.id = a.user_id
                    AND DATE(a.checkin_time) = %s
                WHERE u.role = 1 AND u.is_active = 1
                """

            # Add status filter if specified
            if status_filter == 'present':
                query += " AND a.checkout_time IS NOT NULL AND a.work_duration IS NOT NULL"
            elif status_filter == 'absent':
                query += " AND a.checkin_time IS NULL"
            elif status_filter == 'in_progress':
                query += " AND a.checkin_time IS NOT NULL AND a.checkout_time IS NULL"

            query += " ORDER BY u.firstname, u.lastname"

            cursor.execute(query, (selected_date,))
            records = cursor.fetchall()

            # Calculate summary statistics
            cursor.execute("""
            SELECT 
                COUNT(DISTINCT u.id) as total_users,
                COUNT(DISTINCT CASE WHEN a.checkout_time IS NOT NULL THEN u.id END) as total_present,
                COUNT(DISTINCT CASE WHEN a.checkin_time IS NOT NULL AND a.checkout_time IS NULL THEN u.id END) as in_progress,
                COUNT(DISTINCT CASE WHEN a.checkin_time IS NULL THEN u.id END) as total_absent,
                COUNT(DISTINCT CASE WHEN a.auto_checkout = 1 THEN u.id END) as auto_checkout_count
            FROM t_users_hr u
            LEFT JOIN t_attendance_hr a ON u.id = a.user_id 
                AND DATE(a.checkin_time) = %s
            WHERE u.role = 1 AND u.is_active = 1
            """, (selected_date,))
            summary = cursor.fetchone()

            # Get pending checkouts with error handling
            try:
                pending_checkouts = check_pending_checkouts()
            except Exception as e:
                print(f"Error getting pending checkouts: {e}")
                pending_checkouts = []

    except Exception as e:
        print(f"Error in admin dashboard: {e}")
        records = []
        summary = {'total_users': 0, 'total_present': 0, 'in_progress': 0, 'total_absent': 0, 'auto_checkout_count': 0}
        pending_checkouts = []

    finally:
        conn.close()

    return render_template(
        'dashboard_admin.html',
        records=records,
        selected_date=selected_date,
        today_date=get_ist_date(),  # Use IST date
        summary=summary,
        pending_checkouts=pending_checkouts
    )

def check_pending_checkouts():
    """Check for users who need auto-checkout"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            today = get_ist_date()  # Use IST date instead of datetime.now().date()
            
            # Check if new columns exist
            try:
                cursor.execute("SHOW COLUMNS FROM t_attendance_hr LIKE 'check_in_location_name'")
                new_columns_exist = cursor.fetchone() is not None
            except Exception as e:
                print(f"Column check error in pending checkouts: {e}")
                new_columns_exist = False

            if new_columns_exist:
                cursor.execute("""
                SELECT 
                    a.user_id, 
                    u.firstname, 
                    u.lastname, 
                    a.checkin_time,
                    a.check_in_location_name
                FROM t_attendance_hr a
                JOIN t_users_hr u ON a.user_id = u.id
                WHERE DATE(a.checkin_time) = %s 
                    AND a.checkout_time IS NULL 
                    AND u.role = 1 AND u.is_active = 1
                ORDER BY a.checkin_time
                """, (today,))
            else:
                cursor.execute("""
                SELECT 
                    a.user_id, 
                    u.firstname, 
                    u.lastname, 
                    a.checkin_time,
                    a.location_name as check_in_location_name
                FROM t_attendance_hr a
                JOIN t_users_hr u ON a.user_id = u.id
                WHERE DATE(a.checkin_time) = %s 
                    AND a.checkout_time IS NULL 
                    AND u.role = 1 AND u.is_active = 1
                ORDER BY a.checkin_time
                """, (today,))
            
            return cursor.fetchall()
            
    except Exception as e:
        print(f"Error checking pending checkouts: {e}")
        return []
    finally:
        conn.close()

@app.route('/manual_auto_checkout', methods=['POST'])
def manual_auto_checkout():
    """Manual trigger for auto-checkout (admin only) - Does NOT set auto_checkout=1"""
    if session.get('role') != 2:
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            today = get_ist_date()  # Use IST date
            auto_checkout_time = get_ist_now()  # Use IST time

            # Check if new columns exist
            try:
                cursor.execute("SHOW COLUMNS FROM t_attendance_hr LIKE 'check_in_latitude'")
                new_columns_exist = cursor.fetchone() is not None
            except:
                new_columns_exist = False

            # Count pending checkouts
            cursor.execute("""
            SELECT COUNT(*) as count
            FROM t_attendance_hr a
            JOIN t_users_hr u ON a.user_id = u.id
            WHERE DATE(a.checkin_time) = %s 
                AND a.checkout_time IS NULL 
                AND u.role = 1
            """, (today,))
            count = cursor.fetchone()['count']

            if count > 0:
                if new_columns_exist:
                    # Use new column structure - auto_checkout remains 0 (manual trigger)
                    cursor.execute("""
                    UPDATE t_attendance_hr a
                    JOIN t_users_hr u ON a.user_id = u.id
                    SET a.checkout_time = %s,
                        a.work_duration = TIMEDIFF(%s, a.checkin_time),
                        a.check_out_latitude = a.check_in_latitude,
                        a.check_out_longitude = a.check_in_longitude,
                        a.check_out_location_name = CONCAT('Manual Auto-checkout by Admin: ', COALESCE(a.check_in_location_name, 'Unknown Location'))
                    WHERE DATE(a.checkin_time) = %s 
                        AND a.checkout_time IS NULL 
                        AND u.role = 1
                    """, (auto_checkout_time, auto_checkout_time, today))
                else:
                    # Use old column structure - auto_checkout remains 0 (manual trigger)
                    cursor.execute("""
                    UPDATE t_attendance_hr a
                    JOIN t_users_hr u ON a.user_id = u.id
                    SET a.checkout_time = %s,
                        a.work_duration = TIMEDIFF(%s, a.checkin_time)
                    WHERE DATE(a.checkin_time) = %s 
                        AND a.checkout_time IS NULL 
                        AND u.role = 1
                    """, (auto_checkout_time, auto_checkout_time, today))

                conn.commit()
                return redirect(url_for('admin_dashboard', msg=f'Successfully auto-checked out {count} users'))
            else:
                return redirect(url_for('admin_dashboard', msg='No pending checkouts found'))

    except Exception as e:
        return redirect(url_for('admin_dashboard', error=f'Auto-checkout failed: {e}'))
    finally:
        conn.close()

# SCHEDULED AUTO-CHECKOUT FUNCTION - Sets auto_checkout=1
@scheduler.task('cron', id='auto_checkout_job', hour=23, minute=59, second=59, timezone='Asia/Kolkata')
def scheduled_auto_checkout():
    """Automatically checkout users at 11:59:59 PM IST and set auto_checkout=1"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            today = get_ist_date()  # Use IST date
            checkout_time = get_ist_now()  # Use IST time

            # Check if new columns exist
            try:
                cursor.execute("SHOW COLUMNS FROM t_attendance_hr LIKE 'check_in_latitude'")
                new_columns_exist = cursor.fetchone() is not None
            except:
                new_columns_exist = False

            # Count pending users first
            cursor.execute("""
            SELECT COUNT(*) as count
            FROM t_attendance_hr a
            JOIN t_users_hr u ON u.id = a.user_id
            WHERE DATE(a.checkin_time) = %s 
                AND a.checkout_time IS NULL 
                AND u.role = 1 AND u.is_active = 1
            """, (today,))
            count_result = cursor.fetchone()
            count = count_result['count'] if count_result else 0

            if count > 0:
                if new_columns_exist:
                    # Use new column structure and set auto_checkout = 1
                    cursor.execute("""
                    UPDATE t_attendance_hr a
                    JOIN t_users_hr u ON u.id = a.user_id
                    SET a.checkout_time = %s,
                        a.work_duration = TIMEDIFF(%s, a.checkin_time),
                        a.auto_checkout = 1,
                        a.check_out_latitude = a.check_in_latitude,
                        a.check_out_longitude = a.check_in_longitude,
                        a.check_out_location_name = CONCAT('Scheduled Auto-checkout: ', COALESCE(a.check_in_location_name, 'System'))
                    WHERE DATE(a.checkin_time) = %s 
                        AND a.checkout_time IS NULL 
                        AND u.role = 1 AND u.is_active = 1
                    """, (checkout_time, checkout_time, today))
                else:
                    # Use old column structure and set auto_checkout = 1
                    cursor.execute("""
                    UPDATE t_attendance_hr a
                    JOIN t_users_hr u ON u.id = a.user_id
                    SET a.checkout_time = %s,
                        a.work_duration = TIMEDIFF(%s, a.checkin_time),
                        a.auto_checkout = 1
                    WHERE DATE(a.checkin_time) = %s 
                        AND a.checkout_time IS NULL 
                        AND u.role = 1 AND u.is_active = 1
                    """, (checkout_time, checkout_time, today))

                conn.commit()
                print(f"Scheduled auto-checkout completed for {count} users at {get_ist_now()}")
            else:
                print(f"No pending checkouts found at {get_ist_now()}")

    except Exception as e:
        print(f"Scheduled auto-checkout error: {e}")
    finally:
        conn.close()

@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if session.get('role') != 2:  # Only admin can add users
        return redirect(url_for('login'))

    if request.method == 'POST':
        firstname = request.form['firstname'].strip()
        lastname = request.form['lastname'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        role = int(request.form['role'])
        gender = request.form['gender']
        phone = request.form['phone'].strip()
        department = request.form['department'].strip()
        location = request.form['location'].strip()
        designation = request.form['designation'].strip()
        joining_date = request.form['date_of_joining'].strip()

        # Validation
        if not all([firstname, lastname, email, password, role, gender, phone, department, location, designation, joining_date]):
            return render_template('add_user.html', error="All fields are required", roles=get_roles())

        if gender not in ['male', 'female']:
            return render_template('add_user.html', error="Invalid gender selection", roles=get_roles())

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # Check if email already exists
                cursor.execute("SELECT id FROM t_users_hr WHERE email = %s", (email,))
                if cursor.fetchone():
                    return render_template('add_user.html', error="Email already exists", roles=get_roles())

                # Hash password
                hashed_password = hash_password(password)

                # Insert new user
                cursor.execute("""
                INSERT INTO t_users_hr 
                (firstname, lastname, email, password, role, gender, is_active, phone, department, location, designation, joining_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (firstname, lastname, email, hashed_password, role, gender, 1, phone, department, location, designation, joining_date))

                return render_template('add_user.html', success=f"User {firstname} {lastname} added successfully!", roles=get_roles())

        except Exception as e:
            return render_template('add_user.html', error=f"Error adding user: {str(e)}", roles=get_roles())
        finally:
            conn.close()

    return render_template('add_user.html', roles=get_roles())

def get_roles():
    """Helper function to get all roles"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT role, role_name FROM roles_hr")
            return cursor.fetchall()
    finally:
        conn.close()

@app.route('/user_list')
def user_list():
    if session.get('role') != 2:
        return redirect(url_for('login'))

    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
        SELECT u.id, u.firstname, u.lastname, u.email, r.role_name, u.is_active
        FROM t_users_hr u
        LEFT JOIN roles_hr r ON u.role = r.role
        ORDER BY u.firstname, u.lastname
        ''')
        users = cursor.fetchall()
    conn.close()

    return render_template('user_list.html', users=users)

@app.route('/toggle_user_status/<int:user_id>')
def toggle_user_status(user_id):
    if session.get('role') != 2:
        return redirect(url_for('login'))

    conn = get_db_connection()
    with conn.cursor() as cursor:
        # Get current status
        cursor.execute("SELECT is_active FROM t_users_hr WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        if user:
            new_status = 0 if user['is_active'] else 1
            cursor.execute("UPDATE t_users_hr SET is_active = %s WHERE id = %s", (new_status, user_id))
    
    conn.close()
    return redirect(url_for('user_list'))

@app.route('/user_profile')
def user_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT u.*, r.role_name
        FROM t_users_hr u
        LEFT JOIN roles_hr r ON u.role = r.role
        WHERE u.id = %s
        """, (user_id,))
        user = cursor.fetchone()
    
    conn.close()
    
    if not user:
        return redirect(url_for('login'))
    
    return render_template('user_profile.html', user=user)

@app.route('/monthly_report')
def monthly_report():
    if session.get('role') != 2:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Get month and year from query parameters
    selected_month = request.args.get('month', get_ist_date().strftime('%Y-%m'))
    export_format = request.args.get('export', '')
    
    try:
        year, month = map(int, selected_month.split('-'))
        report_date = date(year, month, 1)
    except ValueError:
        report_date = get_ist_date().replace(day=1)
        selected_month = report_date.strftime('%Y-%m')
    
    # Calculate date range for the month
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    
    with conn.cursor() as cursor:
        # Get all users and their attendance for the month
        cursor.execute("""
        SELECT 
            u.id,
            u.firstname,
            u.lastname,
            COUNT(DISTINCT DATE(a.checkin_time)) as days_present,
            COUNT(DISTINCT CASE WHEN a.checkout_time IS NOT NULL THEN DATE(a.checkin_time) END) as days_completed,
            SUM(TIME_TO_SEC(a.work_duration))/3600 as total_hours,
            AVG(TIME_TO_SEC(a.work_duration))/3600 as avg_hours_per_day,
            MIN(a.checkin_time) as first_checkin,
            MAX(a.checkout_time) as last_checkout,
            COUNT(DISTINCT CASE WHEN a.checkin_time IS NOT NULL AND a.checkout_time IS NULL THEN DATE(a.checkin_time) END) as incomplete_days,
            COUNT(DISTINCT CASE WHEN a.auto_checkout = 1 THEN DATE(a.checkin_time) END) as auto_checkout_days
        FROM t_users_hr u
        LEFT JOIN t_attendance_hr a ON u.id = a.user_id 
            AND DATE(a.checkin_time) >= %s
            AND DATE(a.checkin_time) < %s
        WHERE u.role = 1 AND u.is_active = 1
        GROUP BY u.id, u.firstname, u.lastname
        ORDER BY u.firstname, u.lastname
        """, (report_date, next_month))
        
        monthly_data = cursor.fetchall()
        
        # Calculate working days in the month (excluding weekends)
        cal = calendar.monthcalendar(year, month)
        working_days = sum(1 for week in cal for day in week[0:5] if day != 0)  # Mon-Fri only
        
        # Get total employee count
        cursor.execute("SELECT COUNT(*) as total_employees FROM t_users_hr WHERE role = 1 AND is_active = 1")
        total_employees = cursor.fetchone()['total_employees']
    
    conn.close()
    
    # Calculate summary statistics in Python
    active_employees = len([record for record in monthly_data if record.get('days_present', 0) > 0])
    total_hours_worked = sum(record.get('total_hours', 0) or 0 for record in monthly_data)
    
    summary_stats = {
        'active_employees': active_employees,
        'total_hours_worked': round(total_hours_worked, 1),
        'average_attendance': round(sum(record.get('days_present', 0) or 0 for record in monthly_data) / len(monthly_data) if monthly_data else 0, 1),
        'average_hours_per_employee': round(total_hours_worked / active_employees if active_employees > 0 else 0, 1)
    }
    
    # If export is requested, generate CSV
    if export_format == 'csv':
        return generate_csv_report(monthly_data, selected_month, working_days, total_employees)
    
    return render_template(
        'monthly_report.html',
        monthly_data=monthly_data,
        selected_month=selected_month,
        report_date=report_date,
        working_days=working_days,
        total_employees=total_employees,
        current_date=get_ist_date(),
        summary_stats=summary_stats
    )

def generate_csv_report(monthly_data, selected_month, working_days, total_employees):
    """Generate CSV report for monthly attendance"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Employee Name', 'Days Present', 'Attendance %', 'Total Hours', 'Avg Hours/Day', 'Performance'])
    
    # Write data
    for record in monthly_data:
        attendance_pct = (record['days_present'] / working_days * 100) if working_days > 0 else 0
        performance = 'Excellent' if attendance_pct >= 95 else 'Good' if attendance_pct >= 80 else 'Average' if attendance_pct >= 60 else 'Poor'
        
        writer.writerow([
            f"{record['firstname']} {record['lastname']}",
            f"{record['days_present'] or 0} / {working_days}",
            f"{attendance_pct:.1f}%",
            f"{record['total_hours'] or 0:.1f}h",
            f"{record['avg_hours_per_day'] or 0:.1f}h",
            performance
        ])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=attendance_report_{selected_month}.csv'
    
    return response

@app.context_processor
def inject_current_year():
    return {
        'current_year': get_ist_date().year,
        'ist_now': get_ist_now(),
        'ist_date': get_ist_date()
    }

# At the end of your app.py file
if __name__ == '__main__':
    try:
        conn = get_db_connection()
        print("✅ Connected to the MySQL database.")
        print(f"📊 Database: {app.config['MYSQL_DB']}")
        print(f"🖥️ Host: {app.config['MYSQL_HOST']}")
        conn.close()
    except Exception as e:
        print("❌ Database connection error:", e)
    
    # Render.com uses PORT environment variable
    port = int(os.environ.get('PORT', 10000))  # Render default is 10000
    host = '0.0.0.0'  # Always use 0.0.0.0 for Render
    
    print(f"🚀 Starting Flask app on {host}:{port}")
    print(f"🔧 Debug mode: {app.config.get('DEBUG', False)}")
    print(f"🌍 Environment: {os.environ.get('FLASK_ENV', 'production')}")
    
    # Start scheduler in production
    with app.app_context():
        if not scheduler.running:
            scheduler.start()
            print("📅 Scheduler started - Auto-checkout will run at 23:59:59 IST daily")
    
    app.run(debug=False, port=port, host=host)  # Debug should be False in production
