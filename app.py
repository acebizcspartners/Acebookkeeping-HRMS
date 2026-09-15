from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from functools import wraps
import random
import os
import requests
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Load environment variables from .env file (override=True so edits to .env
# take effect on reload, instead of being masked by env vars inherited from
# the previous process via the Werkzeug auto-reloader)
load_dotenv(override=True)

# Cache for API results - cache_key: (email, from_date, to_date) -> result
_activtrak_cache = {}
_cache_expiry = {}

# Monthly leave accrual rates (in HOURS)
ANNUAL_LEAVE_MONTHLY_CREDIT = 9.25    # 9.25 hours per month (111 hours/year)
SICK_LEAVE_MONTHLY_CREDIT = 7.36     # 7.36 hours per month (88.32 hours/year)

# Until an employee completes this many months they get one sick leave and no
# annual leave; the normal company-wide accrual starts once they cross it.
PROBATION_MONTHS = 6

def months_since(start_date, on_date=None):
    """Whole months elapsed since start_date (a partial month doesn't count)."""
    if on_date is None:
        on_date = datetime.now().date()
    months = (on_date.year - start_date.year) * 12 + (on_date.month - start_date.month)
    if on_date.day < start_date.day:
        months -= 1
    return months

def get_accrued_leave(month=None, date_of_joining=None):
    """Calculate accrued leave hours based on current month (credits start from February).

    An employee still inside their first PROBATION_MONTHS gets exactly one sick
    leave and no annual leave, regardless of the calendar month."""
    if date_of_joining and months_since(date_of_joining) < PROBATION_MONTHS:
        return {
            'annual': 0.0,
            'sick': SICK_LEAVE_MONTHLY_CREDIT
        }

    if month is None:
        month = datetime.now().month
    # Credits start from February, so Jan=0 months, Feb=1 month, Mar=2 months, etc.
    months_accrued = max(0, month - 1)
    return {
        'annual': round(months_accrued * ANNUAL_LEAVE_MONTHLY_CREDIT, 2),
        'sick': round(months_accrued * SICK_LEAVE_MONTHLY_CREDIT, 2)
    }

def validate_password(password):
    """Validate password strength. Returns (is_valid, error_message)"""
    if not password:
        return False, "Password cannot be empty"
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit"
    return True, None

# Database URI: Use PostgreSQL (Neon) if DATABASE_URL is set, else SQLite for local
DATABASE_URL = os.environ.get('DATABASE_URL')
IS_VERCEL = os.environ.get('VERCEL', False)

if DATABASE_URL:
    # Use psycopg2 driver for PostgreSQL (works with Neon)
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    DB_URI = DATABASE_URL
elif IS_VERCEL:
    DB_URI = 'sqlite:////tmp/leave_management.db'
else:
    DB_URI = 'sqlite:///leave_management.db'

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Session timeout configuration (60 minutes inactivity)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Enable SSL for psycopg2 (required by Neon) + Connection pooling
if DATABASE_URL:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'sslmode': 'require'},
        'pool_pre_ping': True,           # Test connections before using them
        'pool_recycle': 3600,            # Recycle connections after 1 hour
        'pool_size': 10,                 # Connection pool size
        'max_overflow': 20,              # Allow overflow connections
    }

# Email configuration - Update these with your SMTP settings
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please login to access this page.'
login_manager.login_message_category = 'info'

# CSRF Protection
csrf = CSRFProtect(app)

# Session timeout handler
@app.before_request
def session_timeout():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=60)
    session.modified = True

    if current_user.is_authenticated:
        session['_user_id'] = current_user.id

# Years whose leave balances this process has already opened, so the check below
# costs one query per process rather than one per request.
_leave_year_opened = set()

@app.before_request
def open_leave_year():
    """On the first request of a new calendar year, roll everyone's closing
    balance forward into the new year."""
    if not current_user.is_authenticated:
        return

    year = datetime.now().year
    if year in _leave_year_opened:
        return

    try:
        created = carry_forward_leave_balances(year)
        _leave_year_opened.add(year)
        if created:
            print(f'[Leave] Opened {year} for {created} employee(s), carrying balances forward')
    except Exception as e:
        db.session.rollback()
        print(f'[Leave] Could not open leave year {year}: {e}')

# Unauthorized handler (session expired)
@login_manager.unauthorized_handler
def unauthorized():
    flash('Your session has expired. Please login again.', 'warning')
    return redirect(url_for('login', timeout='1'))

# CSRF error handler - shows a friendly message instead of a raw 400 page
# when a form is submitted after the session has expired (10 min inactivity)
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash('Your session expired while this page was open. Please log in again and retry.', 'warning')
    return redirect(url_for('login'))

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='employee')  # employee, manager, admin
    department = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Personal Profile fields
    phone = db.Column(db.String(20))
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(20))
    blood_group = db.Column(db.String(10))

    # Address fields
    address = db.Column(db.Text)
    city = db.Column(db.String(50))
    state = db.Column(db.String(50))
    postal_code = db.Column(db.String(20))
    country = db.Column(db.String(50))

    # Emergency Contact
    emergency_contact = db.Column(db.String(100))
    emergency_phone = db.Column(db.String(20))

    # Employment fields
    date_of_joining = db.Column(db.Date)
    designation = db.Column(db.String(100))
    reporting_manager = db.Column(db.String(100))

    # Financial fields
    pan_number = db.Column(db.String(20))
    aadhar_number = db.Column(db.String(20))
    bank_account = db.Column(db.String(50))
    ifsc_code = db.Column(db.String(20))

    def get_leave_balance(self, leave_type):
        """Get leave balance for a specific leave type"""
        balance = LeaveBalance.query.filter_by(
            user_id=self.id,
            year=datetime.now().year
        ).first()

        if not balance:
            return 0

        leave_info = balance.get_available_leave()
        if leave_type == 'annual':
            return leave_info['annual_available']
        elif leave_type == 'sick':
            return leave_info['sick_available']
        return 0

class Leave(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    leave_type = db.Column(db.String(50), nullable=False)  # sick, annual, lwp
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Float, nullable=False, default=0)  # leave hours requested
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected, revoked
    applied_on = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reviewed_on = db.Column(db.DateTime, nullable=True)
    comments = db.Column(db.Text, nullable=True)

    # Revocation fields
    revocation_requested = db.Column(db.Boolean, default=False)
    revocation_reason = db.Column(db.Text, nullable=True)
    revocation_requested_on = db.Column(db.DateTime, nullable=True)

    # Relationships with explicit foreign keys
    user = db.relationship('User', foreign_keys=[user_id], backref='leaves')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by], backref='reviewed_leaves')

class LeaveBalance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    sick_leave_used = db.Column(db.Float, default=0)
    annual_leave_used = db.Column(db.Float, default=0)
    lwp_used = db.Column(db.Float, default=0)
    # Each employee's own entitlement baseline, set by admin from the HR balance
    # sheet. Tenure, carry-forward and mid-year joining all differ per person,
    # so this is stored rather than derived. The monthly credit is added on top
    # of it from accrued_as_of onwards. Falls back to the company-wide accrual
    # calculation when not set.
    annual_accrued = db.Column(db.Float, nullable=True)
    sick_accrued = db.Column(db.Float, nullable=True)
    accrued_as_of = db.Column(db.Date, nullable=True)

    user = db.relationship('User', backref='leave_balances')

    def get_entitlement(self):
        """The employee's entitlement now: stored baseline plus monthly credits since."""
        date_of_joining = self.user.date_of_joining if self.user else None

        # No admin-set balance: fall back to the company-wide calculation, which
        # applies the probation rule (one sick leave, no annual) on its own.
        if self.annual_accrued is None and self.sick_accrued is None:
            accrued = get_accrued_leave(date_of_joining=date_of_joining)
            return accrued['annual'], accrued['sick']

        # Admin has set this employee's balance, so it stands as-is. Monthly
        # credits stay paused until they finish probation.
        if date_of_joining and months_since(date_of_joining) < PROBATION_MONTHS:
            return (self.annual_accrued or 0), (self.sick_accrued or 0)

        # Credits run from the baseline date, or from when probation ended if later
        credit_from = self.accrued_as_of or date(self.year, 1, 1)
        if date_of_joining:
            probation_end = date_of_joining + relativedelta(months=PROBATION_MONTHS)
            probation_end = date(probation_end.year, probation_end.month, 1)
            if probation_end > credit_from:
                credit_from = probation_end

        months_credited = max(0, months_since(credit_from))
        annual = (self.annual_accrued or 0) + months_credited * ANNUAL_LEAVE_MONTHLY_CREDIT
        sick = (self.sick_accrued or 0) + months_credited * SICK_LEAVE_MONTHLY_CREDIT
        return annual, sick

    def get_available_leave(self):
        """Calculate available leave from the employee's entitlement minus what they've used"""
        annual_accrued, sick_accrued = self.get_entitlement()
        return {
            'annual_accrued': round(annual_accrued, 2),
            'sick_accrued': round(sick_accrued, 2),
            'annual_available': round(annual_accrued - self.annual_leave_used, 2),
            'sick_available': round(sick_accrued - self.sick_leave_used, 2),
            'annual_used': self.annual_leave_used,
            'sick_used': self.sick_leave_used,
            'lwp_used': self.lwp_used
        }

def carry_forward_leave_balances(year=None):
    """Open a new leave year for everyone, carrying last year's closing balance
    forward as the new baseline. Safe to call repeatedly - it only creates rows
    that don't exist yet."""
    today = datetime.now().date()
    if year is None:
        year = today.year

    # The balance carried over is whatever it is *today*, so credits must resume
    # from this month - not from January, which would re-credit the months in
    # between if this runs late (e.g. the app wasn't opened until March).
    resume_from = date(today.year, today.month, 1) if today.year == year else date(year, 1, 1)

    created = 0
    for user in User.query.all():
        if LeaveBalance.query.filter_by(user_id=user.id, year=year).first():
            continue

        previous = LeaveBalance.query.filter_by(user_id=user.id, year=year - 1).first()
        if previous:
            closing = previous.get_available_leave()
            annual_baseline = closing['annual_available']
            sick_baseline = closing['sick_available']
        else:
            annual_baseline = sick_baseline = None

        db.session.add(LeaveBalance(
            user_id=user.id,
            year=year,
            annual_accrued=annual_baseline,
            sick_accrued=sick_baseline,
            accrued_as_of=resume_from,
            annual_leave_used=0,
            sick_leave_used=0,
            lwp_used=0
        ))
        created += 1

    if created:
        db.session.commit()
    return created

class PasswordResetOTP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    otp = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_used = db.Column(db.Boolean, default=False)

class LeaveTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    leave_type = db.Column(db.String(50), nullable=False)  # annual, sick
    transaction_type = db.Column(db.String(20), nullable=False)  # credit, debit
    days = db.Column(db.Float, nullable=False)
    balance_after = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    reference_id = db.Column(db.Integer, nullable=True)  # Leave ID for debits
    transaction_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='leave_transactions')

class Salary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    monthly_salary = db.Column(db.Float, nullable=False)
    hourly_rate = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='INR')
    effective_from = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref='salaries')

class PaymentInvoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    salary_amount = db.Column(db.Float, nullable=False)
    deductions = db.Column(db.Float, default=0)
    leave_deduction = db.Column(db.Float, default=0)  # Auto-calculated from approved leaves
    manual_deduction = db.Column(db.Float, default=0)  # Manual deductions (loan, penalty, etc.)
    bonus = db.Column(db.Float, default=0)
    net_amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='INR')
    status = db.Column(db.String(20), default='draft')  # draft, generated, paid
    invoice_number = db.Column(db.String(50), unique=True, nullable=False)
    generated_on = db.Column(db.DateTime, default=datetime.utcnow)
    paid_on = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    user = db.relationship('User', backref='invoices')

class SalaryFinalization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    finalized_on = db.Column(db.DateTime, default=datetime.utcnow)
    finalized_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_employees = db.Column(db.Integer, nullable=False)
    payslips_generated = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text, nullable=True)

    admin = db.relationship('User', backref='salary_finalizations')

class Deduction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.String(200), nullable=False)  # Loan, Penalty, Tax, etc.
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='active')  # active, inactive, removed
    applied_from = db.Column(db.Date, nullable=False)
    applied_to = db.Column(db.Date, nullable=True)  # NULL means ongoing
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id], backref='deductions')
    admin = db.relationship('User', foreign_keys=[created_by])

class LeaveAdjustment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    from_type = db.Column(db.String(50), nullable=False)  # 'annual' or 'sick'
    to_type = db.Column(db.String(50), nullable=False)    # 'annual' or 'sick'
    hours = db.Column(db.Float, nullable=False)
    reason = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id], backref='leave_adjustments')
    admin = db.relationship('User', foreign_keys=[created_by])

class OnboardingDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doc_type = db.Column(db.String(50), nullable=False)  # offer, agreement, assets, increment
    doc_title = db.Column(db.String(200), nullable=False)
    doc_content = db.Column(db.Text, nullable=False)  # HTML content of the document
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Admin who assigned it
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='assigned')  # assigned, acknowledged, rejected

    user = db.relationship('User', foreign_keys=[user_id], backref='onboarding_documents')
    admin = db.relationship('User', foreign_keys=[assigned_by])

class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Designation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EmployeeProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))
    country = db.Column(db.String(100))
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(20))
    designation_id = db.Column(db.Integer, db.ForeignKey('designation.id'))
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'))
    joining_date = db.Column(db.Date)
    employment_type = db.Column(db.String(50))  # Full-time, Part-time, Contract
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    blood_group = db.Column(db.String(5))
    pan_number = db.Column(db.String(20))
    aadhar_number = db.Column(db.String(20))
    bank_account = db.Column(db.String(50))
    ifsc_code = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id], backref='profile')
    designation = db.relationship('Designation', backref='employees')
    department = db.relationship('Department', backref='employees')
    manager = db.relationship('User', foreign_keys=[manager_id])

class EmergencyContact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    relationship = db.Column(db.String(50))
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EmployeeDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    document_type = db.Column(db.String(50), nullable=False)  # Resume, Certificate, etc
    file_name = db.Column(db.String(255))
    file_path = db.Column(db.String(500))
    expiry_date = db.Column(db.Date)
    uploaded_on = db.Column(db.DateTime, default=datetime.utcnow)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), default='present')  # present, absent, annual, sick, lwp
    work_hours = db.Column(db.Float, default=0)  # Total work hours from tracker
    productive_hours = db.Column(db.Float, default=0)  # Productive hours
    screen_time_hours = db.Column(db.Float, default=0)  # Screen time hours
    source = db.Column(db.String(50), default='manual')  # active_tracker, manual
    remarks = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='attendance_records')

class PerformanceReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    review_date = db.Column(db.Date, nullable=False)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    performance_rating = db.Column(db.Float)  # 1-5
    technical_skills = db.Column(db.Float)
    communication = db.Column(db.Float)
    teamwork = db.Column(db.Float)
    comments = db.Column(db.Text)
    goals = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Training(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    html_content = db.Column(db.Text)  # Rich HTML description
    training_type = db.Column(db.String(50), default='video')  # video, document, webinar, live-session
    video_url = db.Column(db.String(500))  # YouTube or other video platform URL
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    trainer = db.Column(db.String(100))
    cost = db.Column(db.Float)
    status = db.Column(db.String(20), default='planned')  # planned, ongoing, completed
    icon = db.Column(db.String(50), default='fa-graduation-cap')  # Font Awesome icon
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EmployeeTraining(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    training_id = db.Column(db.Integer, db.ForeignKey('training.id'), nullable=False)
    status = db.Column(db.String(20), default='enrolled')  # enrolled, completed, dropped
    score = db.Column(db.Float)
    certificate_issued = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EmployeeAsset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    asset_type = db.Column(db.String(100))  # Laptop, Phone, etc
    asset_name = db.Column(db.String(255))
    serial_number = db.Column(db.String(100))
    issue_date = db.Column(db.Date)
    return_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='active')  # active, returned, damaged
    remarks = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    holiday_name = db.Column(db.String(100), nullable=False)
    holiday_date = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text)
    holiday_type = db.Column(db.String(50), default='National')  # National, Regional, Company
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_day_name(self):
        """Get day name (Monday, Tuesday, etc.)"""
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return days[self.holiday_date.weekday()]

def get_default_payroll_month():
    """Which month/year payroll screens should default to.

    Payroll for a month is processed early in the *next* month (e.g. September
    is processed 1-10 October), so during the first 10 days of a month we
    default to last month; after that, to the current month."""
    today = datetime.now().date()
    if today.day <= 10:
        if today.month == 1:
            return 12, today.year - 1
        return today.month - 1, today.year
    return today.month, today.year

def get_expected_working_days(month, year):
    """Count Mon-Fri days in a month, excluding company holidays."""
    from dateutil.relativedelta import relativedelta
    month_start = date(year, month, 1)
    month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)

    holiday_dates = {
        h.holiday_date for h in Holiday.query.filter(
            Holiday.holiday_date >= month_start,
            Holiday.holiday_date <= month_end
        ).all()
    }

    count = 0
    current_date = month_start
    while current_date <= month_end:
        if current_date.weekday() < 5 and current_date not in holiday_dates:
            count += 1
        current_date += timedelta(days=1)
    return count

def auto_mark_attendance(month=None, year=None):
    """Auto-mark all employees as present for the given month (Mon-Fri,
    excluding company holidays). Defaults to get_default_payroll_month()."""
    from dateutil.relativedelta import relativedelta

    if month is None or year is None:
        month, year = get_default_payroll_month()

    month_start = date(year, month, 1)
    month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)

    holiday_dates = {
        h.holiday_date for h in Holiday.query.filter(
            Holiday.holiday_date >= month_start,
            Holiday.holiday_date <= month_end
        ).all()
    }

    employees = User.query.filter_by(role='employee').all()

    for emp in employees:
        current_date = month_start
        while current_date <= month_end:
            # Only Mon-Fri (weekday 0-4), and skip company holidays
            if current_date.weekday() < 5 and current_date not in holiday_dates:
                # Check if attendance already exists
                existing = Attendance.query.filter_by(
                    user_id=emp.id,
                    date=current_date
                ).first()

                if not existing:
                    attendance = Attendance(
                        user_id=emp.id,
                        date=current_date,
                        status='present'
                    )
                    db.session.add(attendance)

            current_date += timedelta(days=1)

    db.session.commit()
    return month, year

def record_leave_transaction(user_id, leave_type, transaction_type, days, description, reference_id=None, transaction_date=None):
    """Record a leave transaction (credit or debit)"""
    if transaction_date is None:
        transaction_date = datetime.now().date()

    # Get current balance
    balance = LeaveBalance.query.filter_by(
        user_id=user_id,
        year=datetime.now().year
    ).first()

    if balance:
        leave_info = balance.get_available_leave()
        if leave_type == 'annual':
            balance_after = leave_info['annual_available']
        elif leave_type == 'sick':
            balance_after = leave_info['sick_available']
        elif leave_type == 'lwp':
            balance_after = leave_info['lwp_used']
        else:
            balance_after = 0
    else:
        balance_after = 0

    transaction = LeaveTransaction(
        user_id=user_id,
        leave_type=leave_type,
        transaction_type=transaction_type,
        days=days,
        balance_after=balance_after,
        description=description,
        reference_id=reference_id,
        transaction_date=transaction_date
    )
    db.session.add(transaction)
    return transaction

def send_n8n_webhook(event, data):
    """Send webhook to n8n for email notifications"""
    webhook_url = os.environ.get('N8N_WEBHOOK_URL')
    if webhook_url:
        try:
            payload = {"event": event, **data}
            requests.post(webhook_url, json=payload, timeout=5)
        except Exception:
            pass

def get_activtrak_working_hours(email, from_date, to_date):
    """Fetch detailed working hours from ActiveTrak computers endpoint with caching and retry logic"""
    api_url = os.environ.get('ACTIVTRAK_API_URL')
    api_key = os.environ.get('ACTIVTRAK_API_KEY')

    if not api_url or not api_key or api_key == 'your_api_key_here':
        return None

    # Create cache key
    cache_key = f"{email}_{from_date}_{to_date}"

    # Check if data is in the fast in-process cache and not expired (1 hour)
    if cache_key in _activtrak_cache:
        if datetime.now() < _cache_expiry.get(cache_key, datetime.now()):
            print(f"[ActiveTrak] Cache hit for {email}")
            return _activtrak_cache[cache_key]
        else:
            # Cache expired, remove it
            del _activtrak_cache[cache_key]
            del _cache_expiry[cache_key]

    headers = {
        'x-api-key': api_key,
        'Content-Type': 'application/json'
    }

    params = {
        'from': from_date.strftime('%Y-%m-%d'),
        'to': to_date.strftime('%Y-%m-%d'),
        # ActiveTrak paginates at ~150 records by default, which silently drops
        # earlier dates once (users x days in range) exceeds that - request a
        # page large enough to cover a full month for the whole org in one call.
        'pageSize': 2000
    }

    # Retry logic - try up to 3 times with increasing timeout
    max_retries = 3
    timeout_values = [20, 30, 45]

    for attempt in range(max_retries):
        try:
            timeout = timeout_values[attempt]
            print(f"[ActiveTrak] Attempt {attempt + 1}/{max_retries} - Timeout: {timeout}s for {email}")

            response = requests.get(api_url, headers=headers, params=params, timeout=timeout)
            response.raise_for_status()

            data = response.json()

            # API returns { activity: [...] } - users endpoint, one record per user per day
            if isinstance(data, dict) and 'activity' in data:
                activities = data['activity']

                # Find all records where this email matches the record's user field (case-insensitive)
                user_computers = [
                    a for a in activities
                    if a.get('user', '').lower() == email.lower()
                ]

                if user_computers:
                    # Sum up time across all computers and days
                    total_active_time = sum(a.get('activeTime', {}).get('total', 0) for a in user_computers)
                    total_passive_time = sum(a.get('passiveTime', {}).get('total', 0) for a in user_computers)
                    total_productive = sum(a.get('activeTime', {}).get('productive', 0) for a in user_computers)
                    total_time = sum(a.get('totalTime', 0) for a in user_computers)
                    total_offline_meetings = sum(a.get('TotalOfflineMeetings', 0) for a in user_computers)

                    # Days with data (totalTime > 0)
                    active_days = [a for a in user_computers if a.get('totalTime', 0) > 0]
                    present_days = len(active_days)

                    # Day-wise breakdown (aggregated across computers per date, for UI display)
                    daily_totals = {}
                    for a in active_days:
                        day_key = a.get('date', '')
                        if not day_key:
                            continue
                        day = daily_totals.setdefault(day_key, {
                            'date': day_key,
                            'active_seconds': 0,
                            'passive_seconds': 0,
                            'productive_seconds': 0,
                            'total_seconds': 0,
                            'first_activity': None,
                            'last_activity': None
                        })
                        day['active_seconds'] += a.get('activeTime', {}).get('total', 0)
                        day['passive_seconds'] += a.get('passiveTime', {}).get('total', 0)
                        day['productive_seconds'] += a.get('activeTime', {}).get('productive', 0)
                        day['total_seconds'] += a.get('totalTime', 0)
                        if a.get('firstActivity') and (not day['first_activity'] or a.get('firstActivity') < day['first_activity']):
                            day['first_activity'] = a.get('firstActivity')
                        if a.get('lastActivity') and (not day['last_activity'] or a.get('lastActivity') > day['last_activity']):
                            day['last_activity'] = a.get('lastActivity')

                    daily_breakdown = [
                        {
                            'date': day['date'],
                            'active_hours': round(day['active_seconds'] / 3600, 2),
                            'idle_hours': round(day['passive_seconds'] / 3600, 2),
                            'productive_hours': round(day['productive_seconds'] / 3600, 2),
                            'total_hours': round(day['total_seconds'] / 3600, 2),
                            'first_activity': day['first_activity'],
                            'last_activity': day['last_activity']
                        }
                        for day in sorted(daily_totals.values(), key=lambda d: d['date'])
                    ]

                    # Get first activity and last activity times
                    first_activity = None
                    last_activity = None
                    for activity in sorted(user_computers, key=lambda x: x.get('date', '')):
                        if activity.get('firstActivity') and not first_activity:
                            first_activity = activity.get('firstActivity')
                        if activity.get('lastActivity'):
                            last_activity = activity.get('lastActivity')

                    # Times are in seconds, convert to hours
                    result = {
                        'total_work_hours': round(total_active_time / 3600, 2),
                        'total_active_time_seconds': total_active_time,
                        'total_passive_time_seconds': total_passive_time,
                        'total_productive_hours': round(total_productive / 3600, 2),
                        'total_screen_hours': round(total_time / 3600, 2),
                        'total_time_seconds': total_time,
                        'present_days': present_days,
                        'total_days': (to_date - from_date).days + 1,
                        'first_activity': first_activity,
                        'last_activity': last_activity,
                        'offline_meetings': total_offline_meetings,
                        'daily_records': active_days,
                        'daily_breakdown': daily_breakdown,
                        'active_vs_idle': {
                            'active': round(total_active_time / 3600, 2),
                            'idle': round(total_passive_time / 3600, 2)
                        }
                    }
                    print(f"[ActiveTrak] Success for {email} on attempt {attempt + 1} - Found {len(user_computers)} computer records")

                    # Cache the result for 1 hour
                    _activtrak_cache[cache_key] = result
                    _cache_expiry[cache_key] = datetime.now() + timedelta(hours=1)

                    return result
                print(f"[ActiveTrak] No matching computer records for {email} - checked {len(activities)} activity entries for {from_date} to {to_date}")
            else:
                print(f"[ActiveTrak] Unexpected API response shape for {email}: keys={list(data.keys()) if isinstance(data, dict) else type(data)}")
            return None

        except requests.exceptions.Timeout:
            print(f"[ActiveTrak] Timeout on attempt {attempt + 1} (timeout={timeout_values[attempt]}s) for {email}")
            if attempt == max_retries - 1:
                print(f"[ActiveTrak] Max retries reached for {email}")
                return None
            continue

        except requests.exceptions.ConnectionError as e:
            print(f"[ActiveTrak] Connection error on attempt {attempt + 1} for {email}: {str(e)}")
            if attempt == max_retries - 1:
                return None
            continue

        except Exception as e:
            print(f"[ActiveTrak] Error on attempt {attempt + 1} for {email}: {str(e)}")
            if attempt == max_retries - 1:
                return None
            continue

    return None

def generate_otp():
    """Generate a 6-digit OTP"""
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp):
    """Send OTP to user's email"""
    try:
        msg = MIMEMultipart()
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = email
        msg['Subject'] = 'Password Reset OTP - Leave Management System'

        body = f"""
        <html>
        <body>
            <h2>Password Reset Request</h2>
            <p>You have requested to reset your password for the Leave Management System.</p>
            <p>Your OTP is: <strong style="font-size: 24px; color: #007bff;">{otp}</strong></p>
            <p>This OTP is valid for 10 minutes.</p>
            <p>If you did not request this, please ignore this email.</p>
            <br>
            <p>Best regards,<br>Leave Management System</p>
        </body>
        </html>
        """

        msg.attach(MIMEText(body, 'html'))

        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        server.starttls()
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['admin', 'manager']:
            flash('Access denied. Admin or Manager privileges required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    # Show session timeout message if user was logged out
    if request.args.get('timeout') == '1':
        flash('Your session has expired. Please login again.', 'warning')

    if request.method == 'POST':
        login_input = request.form.get('login_input')
        password = request.form.get('password')
        # Check for username OR email
        user = User.query.filter(
            (User.username == login_input) | (User.email == login_input)
        ).first()

        if user and check_password_hash(user.password, password):
            login_user(user, remember=False)
            session.permanent = True
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username/email or password', 'error')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        department = request.form.get('department')

        # Validate password
        is_valid, error_msg = validate_password(password)
        if not is_valid:
            flash(error_msg, 'error')
            return render_template('register.html')

        # Check password confirmation
        if password != password_confirm:
            flash('Passwords do not match', 'error')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return render_template('register.html')

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return render_template('register.html')

        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            department=department
        )
        db.session.add(user)
        db.session.commit()

        # Create leave balance for the new user
        balance = LeaveBalance(
            user_id=user.id,
            year=datetime.now().year
        )
        db.session.add(balance)
        db.session.commit()

        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()

        if user:
            # Delete any existing OTPs for this email
            PasswordResetOTP.query.filter_by(email=email, is_used=False).delete()

            # Generate new OTP
            otp = generate_otp()
            expires_at = datetime.utcnow() + timedelta(minutes=10)

            # Save OTP to database
            otp_record = PasswordResetOTP(
                email=email,
                otp=otp,
                expires_at=expires_at
            )
            db.session.add(otp_record)
            db.session.commit()

            # Send OTP email
            if send_otp_email(email, otp):
                session['reset_email'] = email
                flash('OTP has been sent to your email address.', 'success')
                return redirect(url_for('verify_otp'))
            else:
                flash('Failed to send OTP. Please try again.', 'error')
        else:
            # Don't reveal if email exists or not for security
            flash('If an account with this email exists, an OTP has been sent.', 'info')
            return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if 'reset_email' not in session:
        flash('Please enter your email first.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        entered_otp = request.form.get('otp')
        email = session.get('reset_email')

        # Find valid OTP
        otp_record = PasswordResetOTP.query.filter_by(
            email=email,
            otp=entered_otp,
            is_used=False
        ).first()

        if otp_record and otp_record.expires_at > datetime.utcnow():
            session['otp_verified'] = True
            session['otp_id'] = otp_record.id
            flash('OTP verified successfully. Please set your new password.', 'success')
            return redirect(url_for('reset_password'))
        else:
            flash('Invalid or expired OTP. Please try again.', 'error')

    return render_template('verify_otp.html', email=session.get('reset_email'))

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if not session.get('otp_verified') or 'reset_email' not in session:
        flash('Please verify OTP first.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('reset_password.html')

        email = session.get('reset_email')
        user = User.query.filter_by(email=email).first()

        if user:
            user.password = generate_password_hash(password)

            # Mark OTP as used
            otp_id = session.get('otp_id')
            if otp_id:
                otp_record = PasswordResetOTP.query.get(otp_id)
                if otp_record:
                    otp_record.is_used = True

            db.session.commit()

            # Clear session
            session.pop('reset_email', None)
            session.pop('otp_verified', None)
            session.pop('otp_id', None)

            flash('Password reset successfully! Please login with your new password.', 'success')
            return redirect(url_for('login'))

    return render_template('reset_password.html')

@app.route('/resend-otp', methods=['POST'])
def resend_otp():
    if 'reset_email' not in session:
        flash('Please enter your email first.', 'error')
        return redirect(url_for('forgot_password'))

    email = session.get('reset_email')
    user = User.query.filter_by(email=email).first()

    if user:
        # Delete existing OTPs
        PasswordResetOTP.query.filter_by(email=email, is_used=False).delete()

        # Generate new OTP
        otp = generate_otp()
        expires_at = datetime.utcnow() + timedelta(minutes=10)

        otp_record = PasswordResetOTP(
            email=email,
            otp=otp,
            expires_at=expires_at
        )
        db.session.add(otp_record)
        db.session.commit()

        if send_otp_email(email, otp):
            flash('A new OTP has been sent to your email.', 'success')
        else:
            flash('Failed to send OTP. Please try again.', 'error')

    return redirect(url_for('verify_otp'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Get user's leave balance
    balance = LeaveBalance.query.filter_by(
        user_id=current_user.id,
        year=datetime.now().year
    ).first()

    # Get accrued leave info
    leave_info = balance.get_available_leave() if balance else None

    # Get recent leaves
    recent_leaves = Leave.query.filter_by(user_id=current_user.id)\
        .order_by(Leave.applied_on.desc()).limit(5).all()

    # Get pending count for managers/admins
    pending_count = 0
    if current_user.role in ['admin', 'manager']:
        pending_count = Leave.query.filter_by(status='pending').count()

    # Get active trainings for dashboard
    if current_user.role in ['admin', 'manager']:
        # Admins see all active trainings
        active_trainings = Training.query.filter_by(status='ongoing')\
            .order_by(Training.start_date).limit(3).all()
    else:
        # Employees see only their assigned trainings
        assigned_trainings = EmployeeTraining.query.filter_by(user_id=current_user.id).all()
        training_ids = [et.training_id for et in assigned_trainings]
        active_trainings = Training.query.filter(
            Training.id.in_(training_ids),
            Training.status == 'ongoing'
        ).order_by(Training.start_date).limit(3).all() if training_ids else []

    # Get assigned onboarding documents for current user
    assigned_documents = OnboardingDocument.query.filter_by(user_id=current_user.id)\
        .order_by(OnboardingDocument.assigned_at.desc()).all()

    # Get working hours from ActiveTrak API - Current month only
    today = datetime.now().date()
    month_start = date(today.year, today.month, 1)
    if today.month == 12:
        month_end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

    working_hours_summary = None
    all_employees_hours = None

    if current_user.role in ['admin', 'manager']:
        # Admins: Don't fetch data here, load via AJAX after page renders
        # This prevents blocking the page load for 5+ minutes
        all_employees = User.query.filter_by(role='employee').all()
        all_employees_hours = [
            {
                'user_id': emp.id,
                'username': emp.username,
                'email': emp.email,
                'department': emp.department,
                'working_hours': None  # Will be fetched via AJAX
            }
            for emp in all_employees
        ]
        print(f"[Dashboard] Admin view: {len(all_employees_hours)} employees (data will load async)")
    else:
        # Employees see only their own working hours
        working_hours_summary = get_activtrak_working_hours(current_user.email, month_start, month_end)
        if working_hours_summary:
            print(f"[Dashboard] Working hours for {current_user.email}: {working_hours_summary}")
        else:
            print(f"[Dashboard] No working hours data found for {current_user.email} ({month_start} to {month_end})")

    return render_template('dashboard.html',
                         balance=balance,
                         leave_info=leave_info,
                         recent_leaves=recent_leaves,
                         pending_count=pending_count,
                         active_trainings=active_trainings,
                         assigned_documents=assigned_documents,
                         working_hours_summary=working_hours_summary,
                         all_employees_hours=all_employees_hours)

@app.route('/apply-leave', methods=['GET', 'POST'])
@login_required
def apply_leave():
    if request.method == 'POST':
        leave_type = request.form.get('leave_type')
        start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d').date()
        hours = float(request.form.get('hours', 0))
        reason = request.form.get('reason')

        if end_date < start_date:
            flash('End date cannot be before start date', 'error')
            return render_template('apply_leave.html')

        if hours <= 0:
            flash('Please enter valid leave hours', 'error')
            return render_template('apply_leave.html')

        leave = Leave(
            user_id=current_user.id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            hours=hours,
            reason=reason
        )
        db.session.add(leave)
        db.session.commit()

        # Calculate days count
        days_count = (end_date - start_date).days + 1

        # Send n8n webhook for email notification
        send_n8n_webhook('leave_applied', {
            'employee_name': current_user.username,
            'employee_email': current_user.email,
            'leave_type': leave_type,
            'start_date': str(start_date),
            'end_date': str(end_date),
            'hours': hours,
            'days': f'{hours} hrs ({days_count} day{"s" if days_count > 1 else ""})',
            'reason': reason,
            'department': current_user.department
        })

        flash('Leave application submitted successfully!', 'success')
        return redirect(url_for('my_leaves'))

    return render_template('apply_leave.html')

@app.route('/my-leaves')
@login_required
def my_leaves():
    leaves = Leave.query.filter_by(user_id=current_user.id)\
        .order_by(Leave.applied_on.desc()).all()
    return render_template('my_leaves.html', leaves=leaves)

@app.route('/manage-leaves')
@login_required
@admin_required
def manage_leaves():
    status_filter = request.args.get('status', 'pending')
    if status_filter == 'all':
        leaves = Leave.query.order_by(Leave.applied_on.desc()).all()
    elif status_filter == 'revocation':
        leaves = Leave.query.filter_by(revocation_requested=True)\
            .order_by(Leave.revocation_requested_on.desc()).all()
    else:
        leaves = Leave.query.filter_by(status=status_filter)\
            .order_by(Leave.applied_on.desc()).all()
    return render_template('manage_leaves.html', leaves=leaves, status_filter=status_filter)

@app.route('/leave/<int:leave_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_leave(leave_id):
    leave = Leave.query.get_or_404(leave_id)
    comments = request.form.get('comments', '')

    # Approving twice would deduct the balance twice, so only approve a leave
    # that hasn't been deducted yet (pending, or previously rejected).
    if leave.status == 'approved':
        flash('This leave is already approved.', 'warning')
        return redirect(url_for('manage_leaves'))
    if leave.status == 'revoked':
        flash('This leave was revoked and cannot be approved again.', 'warning')
        return redirect(url_for('manage_leaves'))

    leave.status = 'approved'
    leave.reviewed_by = current_user.id
    leave.reviewed_on = datetime.utcnow()
    leave.comments = comments

    # Update leave balance (hours)
    hours = leave.hours
    balance = LeaveBalance.query.filter_by(
        user_id=leave.user_id,
        year=datetime.now().year
    ).first()

    if balance:
        if leave.leave_type == 'sick':
            balance.sick_leave_used += hours
        elif leave.leave_type == 'annual':
            balance.annual_leave_used += hours
        elif leave.leave_type == 'lwp':
            balance.lwp_used += hours

        # Record the debit transaction
        record_leave_transaction(
            user_id=leave.user_id,
            leave_type=leave.leave_type,
            transaction_type='debit',
            days=hours,
            description=f'Leave taken ({leave.start_date.strftime("%d/%m/%Y")} - {leave.end_date.strftime("%d/%m/%Y")}) - {hours} hrs',
            reference_id=leave.id,
            transaction_date=leave.start_date
        )

    # Mark attendance as leave for all days in leave period
    current_date = leave.start_date
    while current_date <= leave.end_date:
        # Skip weekends (Saturday=5, Sunday=6)
        if current_date.weekday() < 5:
            attendance = Attendance.query.filter_by(
                user_id=leave.user_id,
                date=current_date
            ).first()

            if not attendance:
                attendance = Attendance(
                    user_id=leave.user_id,
                    date=current_date,
                    status=leave.leave_type
                )
                db.session.add(attendance)
            else:
                attendance.status = leave.leave_type

        current_date += timedelta(days=1)

    db.session.commit()

    # Notify employee via n8n
    employee = User.query.get(leave.user_id)
    days_count = (leave.end_date - leave.start_date).days + 1
    send_n8n_webhook('leave_approved', {
        'employee_name': employee.username,
        'employee_email': employee.email,
        'leave_type': leave.leave_type,
        'start_date': str(leave.start_date),
        'end_date': str(leave.end_date),
        'hours': hours,
        'days': f'{hours} hrs ({days_count} day{"s" if days_count > 1 else ""})',
        'approved_by': current_user.username
    })

    flash('Leave approved successfully!', 'success')
    return redirect(url_for('manage_leaves'))

@app.route('/leave/<int:leave_id>/reject', methods=['POST'])
@login_required
@admin_required
def reject_leave(leave_id):
    leave = Leave.query.get_or_404(leave_id)
    comments = request.form.get('comments', '')

    if leave.status == 'rejected':
        flash('This leave is already rejected.', 'warning')
        return redirect(url_for('manage_leaves'))

    # Rejecting a leave that was already approved has to give the hours back,
    # otherwise they stay deducted from the employee's balance forever.
    was_approved = leave.status == 'approved'

    leave.status = 'rejected'
    leave.reviewed_by = current_user.id
    leave.reviewed_on = datetime.utcnow()
    leave.comments = comments

    if was_approved:
        hours = leave.hours
        balance = LeaveBalance.query.filter_by(
            user_id=leave.user_id,
            year=datetime.now().year
        ).first()

        if balance:
            if leave.leave_type == 'sick':
                balance.sick_leave_used = max(0, balance.sick_leave_used - hours)
            elif leave.leave_type == 'annual':
                balance.annual_leave_used = max(0, balance.annual_leave_used - hours)
            elif leave.leave_type == 'lwp':
                balance.lwp_used = max(0, balance.lwp_used - hours)

            record_leave_transaction(
                user_id=leave.user_id,
                leave_type=leave.leave_type,
                transaction_type='credit',
                days=hours,
                description=f'Approved leave rejected - restored ({leave.start_date.strftime("%d/%m/%Y")} - {leave.end_date.strftime("%d/%m/%Y")}) - {hours} hrs',
                reference_id=leave.id,
                transaction_date=datetime.now().date()
            )

    db.session.commit()

    # Notify employee via n8n
    employee = User.query.get(leave.user_id)
    days_count = (leave.end_date - leave.start_date).days + 1
    send_n8n_webhook('leave_rejected', {
        'employee_name': employee.username,
        'employee_email': employee.email,
        'leave_type': leave.leave_type,
        'start_date': str(leave.start_date),
        'end_date': str(leave.end_date),
        'hours': leave.hours,
        'days': f'{leave.hours} hrs ({days_count} day{"s" if days_count > 1 else ""})',
        'reason': comments,
        'rejected_by': current_user.username
    })

    flash('Leave rejected.', 'info')
    return redirect(url_for('manage_leaves'))

@app.route('/leave/<int:leave_id>/cancel', methods=['POST'])
@login_required
def cancel_leave(leave_id):
    leave = Leave.query.get_or_404(leave_id)

    if leave.user_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('my_leaves'))

    if leave.status != 'pending':
        flash('Only pending leaves can be cancelled.', 'error')
        return redirect(url_for('my_leaves'))

    db.session.delete(leave)
    db.session.commit()
    flash('Leave application cancelled.', 'success')
    return redirect(url_for('my_leaves'))

@app.route('/leave/<int:leave_id>/request-revocation', methods=['POST'])
@login_required
def request_revocation(leave_id):
    """Employee requests to revoke an approved leave"""
    leave = Leave.query.get_or_404(leave_id)

    if leave.user_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('my_leaves'))

    if leave.status != 'approved':
        flash('Only approved leaves can be revoked.', 'error')
        return redirect(url_for('my_leaves'))

    if leave.revocation_requested:
        flash('Revocation request already submitted.', 'error')
        return redirect(url_for('my_leaves'))

    revocation_reason = request.form.get('revocation_reason', '')

    leave.revocation_requested = True
    leave.revocation_reason = revocation_reason
    leave.revocation_requested_on = datetime.utcnow()

    db.session.commit()
    flash('Revocation request submitted successfully. Awaiting approval.', 'success')
    return redirect(url_for('my_leaves'))

@app.route('/leave/<int:leave_id>/approve-revocation', methods=['POST'])
@login_required
@admin_required
def approve_revocation(leave_id):
    """Manager/Admin approves revocation request and restores leave balance"""
    leave = Leave.query.get_or_404(leave_id)

    if not leave.revocation_requested:
        flash('No revocation request found.', 'error')
        return redirect(url_for('manage_leaves'))

    # Get leave hours
    hours = leave.hours

    # Restore leave balance (hours)
    balance = LeaveBalance.query.filter_by(
        user_id=leave.user_id,
        year=datetime.now().year
    ).first()

    if balance:
        if leave.leave_type == 'sick':
            balance.sick_leave_used = max(0, balance.sick_leave_used - hours)
        elif leave.leave_type == 'annual':
            balance.annual_leave_used = max(0, balance.annual_leave_used - hours)
        elif leave.leave_type == 'lwp':
            balance.lwp_used = max(0, balance.lwp_used - hours)

        # Record the credit transaction (restoration)
        record_leave_transaction(
            user_id=leave.user_id,
            leave_type=leave.leave_type,
            transaction_type='credit',
            days=hours,
            description=f'Leave revoked - restored ({leave.start_date.strftime("%d/%m/%Y")} - {leave.end_date.strftime("%d/%m/%Y")}) - {hours} hrs',
            reference_id=leave.id,
            transaction_date=datetime.now().date()
        )

    # Update leave status
    leave.status = 'revoked'
    leave.revocation_requested = False
    leave.reviewed_on = datetime.utcnow()
    leave.comments = f'Revocation approved. Reason: {leave.revocation_reason}'

    db.session.commit()
    flash('Revocation approved. Leave balance has been restored.', 'success')
    return redirect(url_for('manage_leaves'))

@app.route('/leave/<int:leave_id>/reject-revocation', methods=['POST'])
@login_required
@admin_required
def reject_revocation(leave_id):
    """Manager/Admin rejects revocation request"""
    leave = Leave.query.get_or_404(leave_id)

    if not leave.revocation_requested:
        flash('No revocation request found.', 'error')
        return redirect(url_for('manage_leaves'))

    rejection_reason = request.form.get('rejection_reason', '')

    leave.revocation_requested = False
    leave.comments = f'Revocation rejected. Reason: {rejection_reason}'

    db.session.commit()
    flash('Revocation request rejected.', 'info')
    return redirect(url_for('manage_leaves'))

@app.route('/employees')
@login_required
@admin_required
def employees():
    users = User.query.all()
    return render_template('employees.html', users=users)

@app.route('/employee/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_employee(user_id):
    """Permanently remove an employee who has left the company, along with
    all of their own records (leaves, salary, attendance, etc.)."""
    if user_id == current_user.id:
        flash('You cannot remove your own account.', 'error')
        return redirect(url_for('employees'))

    user = User.query.get_or_404(user_id)

    if user.role == 'admin':
        flash('Admin accounts cannot be removed from here.', 'error')
        return redirect(url_for('employees'))

    # If this person ever acted as the admin/approver on someone ELSE's
    # record (a NOT NULL foreign key), we can't null it out or delete those
    # other employees' records - block instead of corrupting/losing that data.
    blocking = []
    if SalaryFinalization.query.filter_by(finalized_by=user.id).first():
        blocking.append('salary finalizations they performed')
    if Deduction.query.filter_by(created_by=user.id).first():
        blocking.append('deductions they created for other employees')
    if LeaveAdjustment.query.filter_by(created_by=user.id).first():
        blocking.append('leave adjustments they created for other employees')
    if OnboardingDocument.query.filter_by(assigned_by=user.id).first():
        blocking.append('onboarding documents they assigned to other employees')
    if PerformanceReview.query.filter_by(reviewer_id=user.id).first():
        blocking.append('performance reviews they authored for other employees')

    if blocking:
        flash(f"Can't remove {user.username}: they have {', '.join(blocking)} on record. Reassign those first.", 'error')
        return redirect(url_for('employees'))

    try:
        # Clear this user from nullable "who did this" fields on other people's records
        Leave.query.filter_by(reviewed_by=user.id).update({'reviewed_by': None})
        Department.query.filter_by(manager_id=user.id).update({'manager_id': None})
        EmployeeProfile.query.filter_by(manager_id=user.id).update({'manager_id': None})

        # Delete this employee's own records
        LeaveTransaction.query.filter_by(user_id=user.id).delete()
        Leave.query.filter_by(user_id=user.id).delete()
        LeaveBalance.query.filter_by(user_id=user.id).delete()
        PaymentInvoice.query.filter_by(user_id=user.id).delete()
        Salary.query.filter_by(user_id=user.id).delete()
        Deduction.query.filter_by(user_id=user.id).delete()
        LeaveAdjustment.query.filter_by(user_id=user.id).delete()
        OnboardingDocument.query.filter_by(user_id=user.id).delete()
        EmergencyContact.query.filter_by(user_id=user.id).delete()
        EmployeeDocument.query.filter_by(user_id=user.id).delete()
        Attendance.query.filter_by(user_id=user.id).delete()
        PerformanceReview.query.filter_by(user_id=user.id).delete()
        EmployeeTraining.query.filter_by(user_id=user.id).delete()
        EmployeeAsset.query.filter_by(user_id=user.id).delete()
        EmployeeProfile.query.filter_by(user_id=user.id).delete()

        username = user.username
        db.session.delete(user)
        db.session.commit()
        flash(f'{username} has been permanently removed.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not remove employee: {str(e)}', 'error')

    return redirect(url_for('employees'))

@app.route('/employee/<int:user_id>/update-role', methods=['POST'])
@login_required
@admin_required
def update_role(user_id):
    if current_user.role != 'admin':
        flash('Only admins can change roles.', 'error')
        return redirect(url_for('employees'))

    user = User.query.get_or_404(user_id)
    new_role = request.form.get('role')

    if new_role in ['employee', 'manager', 'admin']:
        user.role = new_role
        db.session.commit()
        flash(f'Role updated for {user.username}', 'success')

    return redirect(url_for('employees'))

@app.route('/employee/<int:user_id>/update-employment', methods=['POST'])
@login_required
@admin_required
def update_employment(user_id):
    """Update employee designation, reporting manager, and date of joining"""
    user = User.query.get_or_404(user_id)

    designation = request.form.get('designation')
    reporting_manager = request.form.get('reporting_manager')
    date_of_joining = request.form.get('date_of_joining')

    if designation:
        user.designation = designation
    if reporting_manager:
        user.reporting_manager = reporting_manager
    if date_of_joining:
        user.date_of_joining = datetime.strptime(date_of_joining, '%Y-%m-%d').date()

    db.session.commit()
    flash(f'Employment details updated for {user.username}', 'success')
    return redirect(url_for('employees'))

@app.route('/employee/<int:user_id>/salary', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_employee_salary(user_id):
    user = User.query.get_or_404(user_id)
    salary = Salary.query.filter_by(user_id=user_id, is_active=True).first()

    if request.method == 'POST':
        monthly_salary = float(request.form.get('monthly_salary'))
        hourly_rate = float(request.form.get('hourly_rate'))
        effective_from = datetime.strptime(request.form.get('effective_from'), '%Y-%m-%d').date()

        if salary:
            salary.monthly_salary = monthly_salary
            salary.hourly_rate = hourly_rate
            salary.effective_from = effective_from
            salary.updated_at = datetime.utcnow()
        else:
            salary = Salary(
                user_id=user_id,
                monthly_salary=monthly_salary,
                hourly_rate=hourly_rate,
                effective_from=effective_from
            )
            db.session.add(salary)

        db.session.commit()
        flash(f'Salary updated for {user.username}', 'success')
        return redirect(url_for('view_all_salaries'))

    return render_template('manage_salary.html', user=user, salary=salary)

@app.route('/admin/employee/<int:user_id>/profile')
@login_required
@admin_required
def view_employee_profile(user_id):
    """View comprehensive employee profile with all details"""
    user = User.query.get_or_404(user_id)
    profile = EmployeeProfile.query.filter_by(user_id=user_id).first()
    salary = Salary.query.filter_by(user_id=user_id, is_active=True).first()

    # Leave info
    leave_balance = LeaveBalance.query.filter_by(
        user_id=user_id,
        year=datetime.now().year
    ).first()
    leave_info = leave_balance.get_available_leave() if leave_balance else None

    # Attendance records
    attendance_records = Attendance.query.filter_by(user_id=user_id).order_by(Attendance.date.desc()).limit(30).all()

    # Leave records
    leave_records = Leave.query.filter_by(user_id=user_id).order_by(Leave.start_date.desc()).limit(20).all()

    # Deductions
    deductions = Deduction.query.filter_by(user_id=user_id, status='active').all()

    # Emergency contacts
    emergency_contacts = EmergencyContact.query.filter_by(user_id=user_id).all()

    current_year = datetime.now().year

    return render_template('admin_employee_profile.html',
        user=user,
        profile=profile,
        salary=salary,
        leave_info=leave_info,
        attendance_records=attendance_records,
        leave_records=leave_records,
        deductions=deductions,
        emergency_contacts=emergency_contacts,
        current_year=current_year
    )

@app.route('/admin/employee/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_employee_profile(user_id):
    """Edit employee details"""
    user = User.query.get_or_404(user_id)
    profile = EmployeeProfile.query.filter_by(user_id=user_id).first()

    if request.method == 'POST':
        # Update user info
        user.username = request.form.get('username')
        user.email = request.form.get('email')
        user.department = request.form.get('department')

        # Create or update profile
        if not profile:
            profile = EmployeeProfile(user_id=user_id)
            db.session.add(profile)

        profile.phone = request.form.get('phone')
        profile.address = request.form.get('address')
        profile.city = request.form.get('city')
        profile.state = request.form.get('state')
        profile.postal_code = request.form.get('postal_code')
        profile.country = request.form.get('country')

        dob_str = request.form.get('date_of_birth')
        if dob_str:
            profile.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date()

        profile.gender = request.form.get('gender')
        profile.blood_group = request.form.get('blood_group')

        joining_date_str = request.form.get('joining_date')
        if joining_date_str:
            joining_date = datetime.strptime(joining_date_str, '%Y-%m-%d').date()
            profile.joining_date = joining_date
            user.date_of_joining = joining_date

        profile.employment_type = request.form.get('employment_type')
        profile.pan_number = request.form.get('pan_number')
        profile.aadhar_number = request.form.get('aadhar_number')
        profile.bank_account = request.form.get('bank_account')
        profile.ifsc_code = request.form.get('ifsc_code')

        db.session.commit()
        flash(f'Employee details updated for {user.username}!', 'success')
        return redirect(url_for('view_employee_profile', user_id=user_id))

    departments = Department.query.all()
    designations = Designation.query.all()

    return render_template('edit_employee_profile.html',
        user=user,
        profile=profile,
        departments=departments,
        designations=designations
    )

@app.route('/admin/process-salary', methods=['GET', 'POST'])
@login_required
@admin_required
def process_salary():
    """Bulk salary calculation and processing page"""
    default_month, default_year = get_default_payroll_month()
    month = request.args.get('month', default_month, type=int)
    year = request.args.get('year', default_year, type=int)

    expected_days = get_expected_working_days(month, year)

    employees = User.query.filter_by(role='employee').all()
    employees_data = []
    missing_attendance_count = 0

    for emp in employees:
        salary = Salary.query.filter_by(user_id=emp.id, is_active=True).first()
        if not salary:
            continue

        # Get attendance for this month
        from dateutil.relativedelta import relativedelta
        month_start = date(year, month, 1)
        month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)

        attendance = Attendance.query.filter(
            Attendance.user_id == emp.id,
            Attendance.date >= month_start,
            Attendance.date <= month_end
        ).all()

        present_days = len([a for a in attendance if a.status == 'present'])
        if present_days == 0:
            missing_attendance_count += 1

        # Calculate salary
        monthly_salary = salary.monthly_salary

        # Get active deductions (manual only - leave deductions are handled separately)
        deductions = Deduction.query.filter(
            Deduction.user_id == emp.id,
            Deduction.status == 'active',
            Deduction.applied_from <= month_end,
            (Deduction.applied_to.is_(None) | (Deduction.applied_to >= month_start))
        ).all()
        total_deduction = sum(d.amount for d in deductions)
        net_salary = monthly_salary - total_deduction

        employees_data.append({
            'user': emp,
            'salary': salary,
            'present_days': present_days,
            'expected_days': expected_days,
            'manual_deduction': total_deduction,
            'total_deduction': total_deduction,
            'net_salary': net_salary,
            'monthly_salary': monthly_salary
        })

    month_name = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                  'July', 'August', 'September', 'October', 'November', 'December'][month]

    today = datetime.now().date()
    finalization = SalaryFinalization.query.filter_by(month=month, year=year).first()
    month_has_ended = date(year, month, 1) < date(today.year, today.month, 1)
    can_finalize = finalization is None and month_has_ended

    return render_template('process_salary.html',
        employees_data=employees_data,
        month=month,
        year=year,
        month_name=month_name,
        expected_days=expected_days,
        missing_attendance_count=missing_attendance_count,
        finalization=finalization,
        month_has_ended=month_has_ended,
        can_finalize=can_finalize
    )

@app.route('/admin/finalize-salary/<int:month>/<int:year>', methods=['POST'])
@login_required
@admin_required
def finalize_salary(month, year):
    """Finalize salary and generate payslips for all employees"""

    # Can only finalize a month that has fully ended
    today = datetime.now().date()
    if date(year, month, 1) >= date(today.year, today.month, 1):
        flash('You can only finalize a month after it has fully ended.', 'error')
        return redirect(url_for('process_salary', month=month, year=year))

    # Check if already finalized
    existing = SalaryFinalization.query.filter_by(month=month, year=year).first()
    if existing:
        flash('Salary for this month is already finalized!', 'warning')
        return redirect(url_for('process_salary', month=month, year=year))

    # Get all active employees
    employees = User.query.filter_by(role='employee').all()
    payslips_created = 0

    for emp in employees:
        salary = Salary.query.filter_by(user_id=emp.id, is_active=True).first()
        if not salary:
            continue

        # Check if invoice already exists
        existing_invoice = PaymentInvoice.query.filter_by(
            user_id=emp.id,
            month=month,
            year=year
        ).first()

        if not existing_invoice:
            # Create invoice (payslip)
            invoice_number = f"INV-{emp.id}-{year}-{month:02d}-{datetime.now().strftime('%H%M%S')}"

            # Calculate salary data for this month
            month_start = datetime(year, month, 1).date()
            if month == 12:
                month_end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
            else:
                month_end = datetime(year, month + 1, 1).date() - timedelta(days=1)

            # Get deductions
            deductions = Deduction.query.filter(
                Deduction.user_id == emp.id,
                Deduction.status == 'active',
                Deduction.applied_from <= month_end,
                (Deduction.applied_to.is_(None) | (Deduction.applied_to >= month_start))
            ).all()
            total_deduction = sum(d.amount for d in deductions)

            net_salary = salary.monthly_salary - total_deduction

            invoice = PaymentInvoice(
                user_id=emp.id,
                month=month,
                year=year,
                salary_amount=salary.monthly_salary,
                deductions=total_deduction,
                net_amount=net_salary,
                status='generated',
                invoice_number=invoice_number
            )
            db.session.add(invoice)
            payslips_created += 1

    # Create finalization record
    finalization = SalaryFinalization(
        month=month,
        year=year,
        finalized_by=current_user.id,
        total_employees=len(employees),
        payslips_generated=payslips_created
    )
    db.session.add(finalization)
    db.session.commit()

    flash(f'✅ Salary finalized! {payslips_created} payslips generated for {month}/{year}', 'success')
    return redirect(url_for('process_salary', month=month, year=year))

@app.route('/admin/revert-salary/<int:month>/<int:year>', methods=['POST'])
@login_required
@admin_required
def revert_salary(month, year):
    """Undo a payroll finalization so the month can be corrected and finalized again."""
    finalization = SalaryFinalization.query.filter_by(month=month, year=year).first()
    if not finalization:
        flash('This month is not finalized, so there is nothing to revert.', 'warning')
        return redirect(url_for('process_salary', month=month, year=year))

    invoices = PaymentInvoice.query.filter_by(month=month, year=year).all()

    # A payslip that has already been paid out shouldn't be silently deleted
    paid = [i for i in invoices if i.status == 'paid']
    if paid:
        names = ', '.join(sorted({i.user.username for i in paid if i.user}))
        flash(f"Can't revert: {len(paid)} payslip(s) are already marked paid ({names}). "
              f"Mark them unpaid first if you really need to revert.", 'error')
        return redirect(url_for('process_salary', month=month, year=year))

    for invoice in invoices:
        db.session.delete(invoice)
    db.session.delete(finalization)
    db.session.commit()

    flash(f'Payroll for {month}/{year} reverted - {len(invoices)} payslip(s) removed. '
          f'You can review the figures and finalize again.', 'success')
    return redirect(url_for('process_salary', month=month, year=year))

@app.route('/admin/salaries')
@login_required
@admin_required
def view_all_salaries():
    users = User.query.all()
    employees_data = []

    for user in users:
        salary = Salary.query.filter_by(user_id=user.id, is_active=True).first()
        balance = LeaveBalance.query.filter_by(
            user_id=user.id,
            year=datetime.now().year
        ).first()
        leave_info = balance.get_available_leave() if balance else None

        employees_data.append({
            'user': user,
            'salary': salary,
            'leave_info': leave_info
        })

    return render_template('admin_salaries.html', employees_data=employees_data)

@app.route('/my-payslip')
@login_required
def my_payslip():
    salary = Salary.query.filter_by(user_id=current_user.id, is_active=True).first()
    balance = LeaveBalance.query.filter_by(
        user_id=current_user.id,
        year=datetime.now().year
    ).first()
    leave_info = balance.get_available_leave() if balance else None
    invoices = PaymentInvoice.query.filter_by(user_id=current_user.id).order_by(PaymentInvoice.generated_on.desc()).all()

    return render_template('my_payslip.html', salary=salary, leave_info=leave_info, invoices=invoices)

@app.route('/my-salary')
@login_required
def my_salary():
    # Redirect to new combined payslip page
    return redirect(url_for('my_payslip'))

@app.route('/invoice/generate/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def generate_invoice(user_id):
    user = User.query.get_or_404(user_id)
    salary = Salary.query.filter_by(user_id=user_id, is_active=True).first()

    if not salary:
        flash('Salary not set for this employee.', 'error')
        return redirect(url_for('view_all_salaries'))

    if request.method == 'POST':
        month = int(request.form.get('month'))
        year = int(request.form.get('year'))
        total_deductions = float(request.form.get('deductions', 0))
        bonus = float(request.form.get('bonus', 0))
        notes = request.form.get('notes', '')

        # Calculate leave deduction for this month
        from dateutil.relativedelta import relativedelta
        month_start = date(year, month, 1)
        month_end = (month_start + relativedelta(months=1)) - relativedelta(days=1)

        approved_leaves = Leave.query.filter(
            Leave.user_id == user_id,
            Leave.status == 'approved',
            Leave.start_date >= month_start,
            Leave.end_date <= month_end
        ).all()

        total_leave_hours = sum(leave.hours if leave.hours else 0 for leave in approved_leaves)
        leave_deduction = salary.hourly_rate * total_leave_hours if total_leave_hours > 0 else 0

        # Manual deduction = total deductions - leave deduction
        manual_deduction = max(0, total_deductions - leave_deduction)

        salary_amount = salary.monthly_salary
        net_amount = salary_amount + bonus - total_deductions

        invoice_number = f'INV-{user_id}-{year}-{month:02d}-{datetime.now().strftime("%H%M%S")}'

        invoice = PaymentInvoice(
            user_id=user_id,
            month=month,
            year=year,
            salary_amount=salary_amount,
            deductions=total_deductions,
            leave_deduction=leave_deduction,
            manual_deduction=manual_deduction,
            bonus=bonus,
            net_amount=net_amount,
            invoice_number=invoice_number,
            status='generated',
            notes=notes
        )
        db.session.add(invoice)
        db.session.commit()

        flash(f'Invoice generated: {invoice_number}', 'success')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))

    current_month = datetime.now().month
    current_year = datetime.now().year

    # Get active deductions for this employee
    today = datetime.now().date()
    active_deductions = Deduction.query.filter(
        Deduction.user_id == user_id,
        Deduction.status == 'active',
        Deduction.applied_from <= today,
        (Deduction.applied_to.is_(None) | (Deduction.applied_to >= today))
    ).all()

    total_deductions = sum(d.amount for d in active_deductions)

    # Calculate leave deduction for current month
    from dateutil.relativedelta import relativedelta

    month_start = date(current_year, current_month, 1)
    month_end = (month_start + relativedelta(months=1)) - relativedelta(days=1)

    # Get approved leaves for this month
    approved_leaves = Leave.query.filter(
        Leave.user_id == user_id,
        Leave.status == 'approved',
        Leave.start_date >= month_start,
        Leave.end_date <= month_end
    ).all()

    # Calculate total leave hours
    total_leave_hours = sum(leave.hours if leave.hours else 0 for leave in approved_leaves)

    # Calculate leave deduction (hourly rate * leave hours)
    leave_deduction = salary.hourly_rate * total_leave_hours if total_leave_hours > 0 else 0

    return render_template('generate_invoice.html', user=user, salary=salary,
                         current_month=current_month, current_year=current_year,
                         active_deductions=active_deductions,
                         total_deductions=total_deductions,
                         approved_leaves=approved_leaves,
                         total_leave_hours=total_leave_hours,
                         leave_deduction=leave_deduction)

@app.route('/invoice/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    invoice = PaymentInvoice.query.get_or_404(invoice_id)

    if current_user.role != 'admin' and current_user.id != invoice.user_id:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))

    user = User.query.get(invoice.user_id)
    salary = Salary.query.filter_by(user_id=invoice.user_id, is_active=True).first()

    return render_template('view_invoice.html', invoice=invoice, user=user, salary=salary)

@app.route('/invoices')
@login_required
def my_invoices():
    invoices = PaymentInvoice.query.filter_by(user_id=current_user.id)\
        .order_by(PaymentInvoice.generated_on.desc()).all()
    return render_template('my_invoices.html', invoices=invoices)

@app.route('/admin/invoices')
@login_required
@admin_required
def admin_invoices():
    invoices = PaymentInvoice.query.order_by(PaymentInvoice.generated_on.desc()).all()
    return render_template('admin_invoices.html', invoices=invoices)

@app.route('/invoice/<int:invoice_id>/mark-paid', methods=['POST'])
@login_required
@admin_required
def mark_invoice_paid(invoice_id):
    invoice = PaymentInvoice.query.get_or_404(invoice_id)
    invoice.status = 'paid'
    invoice.paid_on = datetime.utcnow()
    db.session.commit()
    flash(f'Invoice {invoice.invoice_number} marked as paid.', 'success')
    return redirect(url_for('view_invoice', invoice_id=invoice.id))

@app.route('/leave-balance')
@login_required
def leave_balance():
    balance = LeaveBalance.query.filter_by(
        user_id=current_user.id,
        year=datetime.now().year
    ).first()
    leave_info = balance.get_available_leave() if balance else None
    current_month = datetime.now().month
    return render_template('leave_balance.html', balance=balance, leave_info=leave_info, current_month=current_month)

@app.route('/leave-transactions')
@login_required
def leave_transactions():
    leave_type = request.args.get('type', 'annual')  # Default to annual leave
    year = request.args.get('year', datetime.now().year, type=int)

    # Get user's leave balance
    balance = LeaveBalance.query.filter_by(
        user_id=current_user.id,
        year=year
    ).first()
    leave_info = balance.get_available_leave() if balance else None

    # Get transactions from database (debits from approved leaves)
    db_transactions = LeaveTransaction.query.filter_by(
        user_id=current_user.id,
        leave_type=leave_type
    ).filter(
        db.extract('year', LeaveTransaction.transaction_date) == year
    ).order_by(LeaveTransaction.transaction_date.desc()).all()

    # Generate monthly credit transactions
    current_month = datetime.now().month if year == datetime.now().year else 12

    # Build complete transaction list with credits and debits
    transactions = []
    running_balance = 0

    # Add monthly credits (LWP has no credits)
    if leave_type in ['annual', 'sick']:
        credit_rate = ANNUAL_LEAVE_MONTHLY_CREDIT if leave_type == 'annual' else SICK_LEAVE_MONTHLY_CREDIT
        for month in range(2, current_month + 1):  # Credits start from February
            credit_date = datetime(year, month, 1).date()
            running_balance += credit_rate
            transactions.append({
                'date': credit_date,
                'type': 'credit',
                'days': credit_rate,
                'description': f'Monthly credit for {credit_date.strftime("%B %Y")}',
                'balance': round(running_balance, 2)
            })

    # Add debits from database
    for txn in db_transactions:
        if txn.transaction_type == 'debit':
            running_balance -= txn.days
            transactions.append({
                'date': txn.transaction_date,
                'type': 'debit',
                'days': txn.days,
                'description': txn.description,
                'balance': round(running_balance, 2)
            })

    # Sort by date descending
    transactions.sort(key=lambda x: x['date'], reverse=True)

    # Recalculate running balance in chronological order
    transactions_chrono = sorted(transactions, key=lambda x: x['date'])
    running_balance = 0
    for txn in transactions_chrono:
        if txn['type'] == 'credit':
            running_balance += txn['days']
        else:
            running_balance -= txn['days']
        txn['balance'] = round(running_balance, 2)

    # Reverse back to show newest first
    transactions.sort(key=lambda x: x['date'], reverse=True)

    return render_template('leave_transactions.html',
                         transactions=transactions,
                         leave_type=leave_type,
                         leave_info=leave_info,
                         year=year,
                         current_year=datetime.now().year)

# API endpoints for AJAX calls
@app.route('/api/leave-stats')
@login_required
def leave_stats():
    balance = LeaveBalance.query.filter_by(
        user_id=current_user.id,
        year=datetime.now().year
    ).first()

    if balance:
        leave_info = balance.get_available_leave()
        return jsonify({
            'sick': {'accrued': leave_info['sick_accrued'], 'used': leave_info['sick_used'], 'available': leave_info['sick_available']},
            'annual': {'accrued': leave_info['annual_accrued'], 'used': leave_info['annual_used'], 'available': leave_info['annual_available']},
            'lwp': {'used': leave_info['lwp_used']}
        })
    return jsonify({})

@app.route('/api/working-hours')
@login_required
def api_working_hours():
    """Get working hours for current user from ActiveTrak API"""
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')

    if not from_date_str or not to_date_str:
        today = datetime.now().date()
        from_date = date(today.year, today.month, 1)
        if today.month == 12:
            to_date = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            to_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
    else:
        from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
        to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()

    working_hours = get_activtrak_working_hours(current_user.email, from_date, to_date)

    if working_hours:
        return jsonify(working_hours)
    return jsonify({'error': 'Unable to fetch working hours from ActiveTrak'}), 503

@app.route('/api/working-hours/employee/<int:user_id>')
@login_required
@admin_required
def api_employee_working_hours(user_id):
    """Get working hours for specific employee (admin only)"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')

    if not from_date_str or not to_date_str:
        today = datetime.now().date()
        from_date = date(today.year, today.month, 1)
        if today.month == 12:
            to_date = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            to_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
    else:
        from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
        to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()

    working_hours = get_activtrak_working_hours(user.email, from_date, to_date)

    if working_hours:
        return jsonify(working_hours)
    return jsonify({'error': 'Unable to fetch working hours from ActiveTrak'}), 503

@app.route('/api/employee-working-hours/<int:user_id>')
@login_required
@admin_required
def get_employee_working_hours_api(user_id):
    """Get working hours for specific employee (AJAX endpoint)"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    today = datetime.now().date()
    month_start = date(today.year, today.month, 1)
    if today.month == 12:
        month_end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

    working_hours = get_activtrak_working_hours(user.email, month_start, month_end)

    return jsonify({
        'user_id': user_id,
        'email': user.email,
        'working_hours': working_hours
    })

@app.route('/api/debug/working-hours')
@login_required
def debug_working_hours():
    """Debug endpoint to check raw API data"""
    api_url = os.environ.get('ACTIVTRAK_API_URL')
    api_key = os.environ.get('ACTIVTRAK_API_KEY')

    today = datetime.now().date()
    from_date = date(today.year, today.month, 1)
    if today.month == 12:
        to_date = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        to_date = date(today.year, today.month + 1, 1) - timedelta(days=1)

    try:
        headers = {
            'x-api-key': api_key,
            'Content-Type': 'application/json'
        }

        params = {
            'from': from_date.strftime('%Y-%m-%d'),
            'to': to_date.strftime('%Y-%m-%d')
        }

        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        # Find current user's data
        if isinstance(data, dict) and 'activity' in data:
            activities = data['activity']
            user_records = [a for a in activities if a.get('user', '').lower() == current_user.email.lower()]

            return jsonify({
                'status': 'success',
                'current_user_email': current_user.email,
                'date_range': f"{from_date} to {to_date}",
                'total_records_in_api': len(data.get('activity', [])),
                'user_records_found': len(user_records),
                'sample_user_records': user_records[:3] if user_records else [],
                'sample_all_records': data.get('activity', [])[:2] if data.get('activity') else [],
                'summary': get_activtrak_working_hours(current_user.email, from_date, to_date)
            })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'error_type': type(e).__name__
        }), 500

@app.route('/admin/all-employees-working-hours')
@login_required
@admin_required
def all_employees_working_hours():
    """View all employees' working hours for current month (admin only)"""
    today = datetime.now().date()
    from_date = date(today.year, today.month, 1)
    if today.month == 12:
        to_date = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        to_date = date(today.year, today.month + 1, 1) - timedelta(days=1)

    employees = User.query.filter_by(role='employee').all()
    employees_data = []

    for emp in employees:
        if emp.email:
            working_hours = get_activtrak_working_hours(emp.email, from_date, to_date)
            employees_data.append({
                'user_id': emp.id,
                'username': emp.username,
                'email': emp.email,
                'department': emp.department,
                'working_hours': working_hours
            })

    return render_template('admin_employees_working_hours.html',
                         employees_data=employees_data,
                         month_year=f"{today.strftime('%B %Y')}")

# HRMS FEATURES

# Attendance Management
@app.route('/admin/auto-mark-attendance', methods=['POST'])
@login_required
@admin_required
def auto_mark_attendance_route():
    """Auto-mark all employees as present for the selected month (Mon-Fri, excluding holidays)"""
    month = request.form.get('month', type=int)
    year = request.form.get('year', type=int)
    if not month or not year:
        month, year = get_default_payroll_month()

    month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']

    try:
        marked_month, marked_year = auto_mark_attendance(month, year)
        flash(f'Attendance auto-marked for all employees (Mon-Fri, excluding holidays) for {month_names[marked_month]} {marked_year}!', 'success')
    except Exception as e:
        flash(f'Error marking attendance: {str(e)}', 'error')
        marked_month, marked_year = month, year

    return redirect(url_for('process_salary', month=marked_month, year=marked_year))

@app.route('/admin/activtrak-test', methods=['GET'])
@login_required
@admin_required
def activtrak_test():
    """Test ActiveTrak API integration - debug endpoint"""
    try:
        today = datetime.now().date()
        from_date = date(today.year, today.month, 1)
        if today.month == 12:
            to_date = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            to_date = date(today.year, today.month + 1, 1) - timedelta(days=1)

        api_url = os.environ.get('ACTIVTRAK_API_URL')
        api_key = os.environ.get('ACTIVTRAK_API_KEY')

        if not api_url or not api_key:
            return jsonify({'error': 'API configuration missing'}), 400

        headers = {
            'x-api-key': api_key,
            'Content-Type': 'application/json'
        }

        params = {
            'from': from_date.strftime('%Y-%m-%d'),
            'to': to_date.strftime('%Y-%m-%d')
        }

        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        raw_data = response.json()

        return jsonify({
            'status': 'success',
            'message': 'ActiveTrak API connection successful',
            'request_params': params,
            'response_sample': raw_data[:1] if isinstance(raw_data, list) else raw_data,
            'total_records': len(raw_data) if isinstance(raw_data, list) else 'Not a list',
            'raw_response': raw_data
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'error_type': type(e).__name__
        }), 500

# Employee Profile
# Emergency Contacts
@app.route('/emergency-contacts')
@login_required
def emergency_contacts():
    contacts = EmergencyContact.query.filter_by(user_id=current_user.id).all()
    return render_template('emergency_contacts.html', contacts=contacts)

@app.route('/emergency-contact/add', methods=['POST'])
@login_required
def add_emergency_contact():
    contact = EmergencyContact(
        user_id=current_user.id,
        name=request.form.get('name'),
        relationship=request.form.get('relationship'),
        phone=request.form.get('phone'),
        email=request.form.get('email')
    )
    db.session.add(contact)
    db.session.commit()
    flash('Emergency contact added!', 'success')
    return redirect(url_for('emergency_contacts'))

@app.route('/emergency-contact/<int:contact_id>/delete', methods=['POST'])
@login_required
def delete_emergency_contact(contact_id):
    contact = EmergencyContact.query.get_or_404(contact_id)
    if contact.user_id != current_user.id:
        flash('Unauthorized', 'error')
        return redirect(url_for('emergency_contacts'))

    db.session.delete(contact)
    db.session.commit()
    flash('Emergency contact deleted!', 'success')
    return redirect(url_for('emergency_contacts'))

# Admin: Performance Reviews
@app.route('/admin/performance-reviews')
@login_required
@admin_required
def performance_reviews():
    reviews = PerformanceReview.query.order_by(PerformanceReview.review_date.desc()).all()
    users = User.query.all()
    return render_template('performance_reviews.html', reviews=reviews, users=users)

@app.route('/admin/performance-review/add', methods=['POST'])
@login_required
@admin_required
def add_performance_review():
    review = PerformanceReview(
        user_id=int(request.form.get('user_id')),
        reviewer_id=current_user.id,
        review_date=datetime.now().date(),
        period_start=datetime.strptime(request.form.get('period_start'), '%Y-%m-%d').date(),
        period_end=datetime.strptime(request.form.get('period_end'), '%Y-%m-%d').date(),
        performance_rating=float(request.form.get('performance_rating')),
        technical_skills=float(request.form.get('technical_skills')),
        communication=float(request.form.get('communication')),
        teamwork=float(request.form.get('teamwork')),
        comments=request.form.get('comments'),
        goals=request.form.get('goals')
    )
    db.session.add(review)
    db.session.commit()
    flash('Performance review added!', 'success')
    return redirect(url_for('performance_reviews'))

# Employee: View Training Programs
@app.route('/training')
@login_required
def training_list():
    """View training programs - accessible to all employees"""
    trainings = Training.query.all()
    return render_template('training.html', trainings=trainings)

# Admin: Training Management
@app.route('/admin/training')
@login_required
@admin_required
def training_admin():
    """View training programs - accessible to all users"""

    if current_user.role in ['admin', 'manager']:
        # Admins see all trainings
        trainings = Training.query.all()
    else:
        # Employees see only trainings assigned to them
        assigned_trainings = EmployeeTraining.query.filter_by(user_id=current_user.id).all()
        training_ids = [et.training_id for et in assigned_trainings]
        trainings = Training.query.filter(Training.id.in_(training_ids)).order_by(Training.start_date).all() if training_ids else []

    return render_template('training.html', trainings=trainings)

@app.route('/admin/training/add', methods=['POST'])
@login_required
@admin_required
def add_training():
    training_type = request.form.get('training_type', 'video')

    # Map training type to icon
    type_icons = {
        'video': 'fa-video',
        'document': 'fa-file-pdf',
        'webinar': 'fa-webcam',
        'live-session': 'fa-users',
        'workshop': 'fa-tools'
    }

    training = Training(
        title=request.form.get('title'),
        description=request.form.get('description'),
        html_content=request.form.get('html_content'),
        training_type=training_type,
        video_url=request.form.get('video_url'),
        icon=type_icons.get(training_type, 'fa-graduation-cap'),
        start_date=datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date(),
        end_date=datetime.strptime(request.form.get('end_date'), '%Y-%m-%d').date(),
        trainer=request.form.get('trainer'),
        status=request.form.get('status', 'ongoing'),
        cost=0.0
    )
    db.session.add(training)
    db.session.commit()

    # Auto-assign training to all employees
    all_users = User.query.filter_by(role='employee').all()
    for user in all_users:
        employee_training = EmployeeTraining(
            user_id=user.id,
            training_id=training.id,
            status='enrolled'
        )
        db.session.add(employee_training)
    db.session.commit()

    flash('Training added and assigned to all employees!', 'success')
    return redirect(url_for('training_admin'))

@app.route('/admin/training/<int:training_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_training(training_id):
    training = Training.query.get_or_404(training_id)
    title = training.title
    db.session.delete(training)
    db.session.commit()
    flash(f'Training "{title}" deleted successfully!', 'success')
    return redirect(url_for('training_admin'))

# Admin: Salary Deductions Management
@app.route('/admin/deductions')
@login_required
@admin_required
def manage_deductions():
    employees = User.query.filter_by(role='employee').all()
    deductions = Deduction.query.filter_by(status='active').order_by(Deduction.created_at.desc()).all()
    today = datetime.now().date()
    return render_template('manage_deductions.html', employees=employees, deductions=deductions, today=today)

@app.route('/admin/deduction/add', methods=['POST'])
@login_required
@admin_required
def add_deduction():
    user_id = int(request.form.get('user_id'))
    reason = request.form.get('reason', '')
    amount = float(request.form.get('amount', 0))
    applied_from_str = request.form.get('applied_from')
    applied_to_str = request.form.get('applied_to')
    description = request.form.get('description', '')

    # Parse dates
    if applied_from_str:
        applied_from = datetime.strptime(applied_from_str, '%Y-%m-%d').date()
    else:
        applied_from = datetime.now().date()

    if applied_to_str:
        applied_to = datetime.strptime(applied_to_str, '%Y-%m-%d').date()
    else:
        applied_to = None

    # If reason is empty (from Leave Deduction tab), leave it as is
    if not reason:
        reason = 'Deduction'

    deduction = Deduction(
        user_id=user_id,
        reason=reason,
        amount=amount,
        applied_from=applied_from,
        applied_to=applied_to,
        description=description,
        created_by=current_user.id,
        status='active'
    )
    db.session.add(deduction)
    db.session.commit()

    employee = User.query.get(user_id)
    flash(f'Deduction of ₹{amount} added for {employee.username}!', 'success')
    return redirect(url_for('manage_deductions'))

@app.route('/admin/deduction/<int:deduction_id>/remove', methods=['POST'])
@login_required
@admin_required
def remove_deduction(deduction_id):
    deduction = Deduction.query.get_or_404(deduction_id)
    employee = deduction.user
    amount = deduction.amount

    deduction.status = 'removed'
    db.session.commit()

    flash(f'Deduction of ₹{amount} removed for {employee.username}!', 'success')
    return redirect(url_for('manage_deductions'))

# Admin: Manage Leave Balance
@app.route('/admin/manage-leave-balance')
@login_required
@admin_required
def manage_leave_balance():
    """View employees with minus leave balances"""
    employees = User.query.filter_by(role='employee').all()
    employees_with_minus = []

    current_month_start = datetime.now().replace(day=1).date()
    if datetime.now().month == 12:
        current_month_end = datetime.now().replace(year=datetime.now().year + 1, month=1, day=1).date() - timedelta(days=1)
    else:
        current_month_end = datetime.now().replace(month=datetime.now().month + 1, day=1).date() - timedelta(days=1)

    for emp in employees:
        annual_balance = emp.get_leave_balance('annual')
        sick_balance = emp.get_leave_balance('sick')

        if annual_balance < 0 or sick_balance < 0:
            # Check if deduction was already created this month for negative leave
            recent_deduction = Deduction.query.filter(
                Deduction.user_id == emp.id,
                Deduction.reason.like('%Negative%'),
                Deduction.applied_from >= current_month_start,
                Deduction.applied_from <= current_month_end
            ).first()

            action_taken = None
            if recent_deduction:
                action_taken = 'Deducted'

            employees_with_minus.append({
                'user': emp,
                'annual_balance': annual_balance,
                'sick_balance': sick_balance,
                'has_minus': annual_balance < 0 or sick_balance < 0,
                'action_taken': action_taken
            })

    return render_template('manage_leave_balance.html', employees=employees_with_minus, all_employees=User.query.filter_by(role='employee').all())

@app.route('/admin/deduct-minus-leave/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def deduct_minus_leave(user_id):
    """Deduct salary for negative leave balance"""
    user = User.query.get_or_404(user_id)
    salary = Salary.query.filter_by(user_id=user_id, is_active=True).first()

    if not salary:
        flash('Salary not configured for this employee.', 'error')
        return redirect(url_for('manage_leave_balance'))

    annual_balance = user.get_leave_balance('annual')
    sick_balance = user.get_leave_balance('sick')

    created_deductions = False

    if annual_balance < 0:
        minus_hours = abs(annual_balance)
        deduction_amount = salary.hourly_rate * minus_hours

        deduction = Deduction(
            user_id=user_id,
            reason=f'Salary Deduction - Negative Annual Leave ({minus_hours} hrs)',
            amount=deduction_amount,
            status='active',
            applied_from=datetime.now().date(),
            description=f'Deduction for negative annual leave: {minus_hours} hours',
            created_by=current_user.id
        )
        db.session.add(deduction)
        created_deductions = True

    if sick_balance < 0:
        minus_hours = abs(sick_balance)
        deduction_amount = salary.hourly_rate * minus_hours

        deduction = Deduction(
            user_id=user_id,
            reason=f'Salary Deduction - Negative Sick Leave ({minus_hours} hrs)',
            amount=deduction_amount,
            status='active',
            applied_from=datetime.now().date(),
            description=f'Deduction for negative sick leave: {minus_hours} hours',
            created_by=current_user.id
        )
        db.session.add(deduction)
        created_deductions = True

    if created_deductions:
        # Reset leave balance back to 0 after deducting salary
        balance = LeaveBalance.query.filter_by(
            user_id=user_id,
            year=datetime.now().year
        ).first()

        if balance:
            if annual_balance < 0:
                # Reset annual leave back to 0
                balance.annual_balance = 0
            if sick_balance < 0:
                # Reset sick leave back to 0
                balance.sick_balance = 0

        db.session.commit()
        flash(f'✅ Salary deduction created for {user.username}! Leave balance reset to 0.', 'success')
    else:
        flash('No negative balance found for this employee.', 'info')

    return redirect(url_for('manage_leave_balance'))

@app.route('/admin/carry-forward-leave/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def carry_forward_leave(user_id):
    """Carry forward negative leave balance to next month (no salary deduction)"""
    user = User.query.get_or_404(user_id)

    flash(f'✅ Negative leave balance for {user.username} marked for carry forward. No salary deduction applied.', 'success')
    return redirect(url_for('manage_leave_balance'))

@app.route('/admin/undo-deduction/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def undo_deduction(user_id):
    """Undo salary deduction and restore negative leave balance"""
    user = User.query.get_or_404(user_id)

    # Find and delete the deduction for negative leave created this month
    current_month_start = datetime.now().replace(day=1).date()
    if datetime.now().month == 12:
        current_month_end = datetime.now().replace(year=datetime.now().year + 1, month=1, day=1).date() - timedelta(days=1)
    else:
        current_month_end = datetime.now().replace(month=datetime.now().month + 1, day=1).date() - timedelta(days=1)

    deduction = Deduction.query.filter(
        Deduction.user_id == user_id,
        Deduction.reason.like('%Negative%'),
        Deduction.applied_from >= current_month_start,
        Deduction.applied_from <= current_month_end
    ).first()

    if deduction:
        db.session.delete(deduction)
        db.session.commit()
        flash(f'✅ Deduction undone for {user.username}. Leave balance restored.', 'success')
    else:
        flash('No deduction found to undo for this employee.', 'info')

    return redirect(url_for('manage_leave_balance'))

# Admin: Employee Onboarding
@app.route('/admin/employee-onboarding')
@login_required
@admin_required
def employee_onboarding():
    """Employee onboarding page - generate all documents for new employee"""
    employees = User.query.filter_by(role='employee').all()
    departments = Department.query.all()

    return render_template('employee_onboarding.html',
        employees=employees,
        departments=departments,
        company_name='Ace Bookkeeping Private Limited',
        company_cin='U69201DC2026FTC469983',
        company_address='1/27, 1st Floor, Mall Road, Tilak Nagar (West Delhi), New Delhi, West Delhi - 110018, Delhi',
        company_phone='98110 08636',
        company_email='info.acebookkeeping@gmail.com'
    )

def to_ddmmyyyy(iso_date_str):
    """Convert a 'YYYY-MM-DD' form value to 'DD/MM/YYYY' for display; leaves
    non-ISO or empty input unchanged rather than raising."""
    if not iso_date_str:
        return iso_date_str
    try:
        return datetime.strptime(iso_date_str, '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return iso_date_str

@app.route('/admin/generate-onboarding-document', methods=['POST'])
@login_required
@admin_required
def generate_onboarding_document():
    """Generate onboarding document (offer letter, agreement, etc.)"""
    try:
        doc_type = request.form.get('doc_type')
        employee_id = request.form.get('employee_id')

        if not doc_type or not employee_id:
            flash('Please select employee and document type', 'error')
            return redirect(url_for('employee_onboarding'))

        employee_id = int(employee_id)
        employee = User.query.get_or_404(employee_id)
        salary = Salary.query.filter_by(user_id=employee_id, is_active=True).first()

        if not salary:
            flash('Salary not configured for this employee', 'error')
            return redirect(url_for('employee_onboarding'))
    except Exception as e:
        flash(f'Error loading employee data: {str(e)}', 'error')
        return redirect(url_for('employee_onboarding'))

    # Calculate salary components (assuming 50% basic, 50% of basic as HRA, rest special)
    monthly = salary.monthly_salary
    basic = round(monthly * 0.5)
    hra = round(basic * 0.5)
    special = monthly - basic - hra

    doc_data = {
        'doc_type': doc_type,
        'employee': employee,
        'salary': salary,
        'monthly_salary': monthly,
        'basic': basic,
        'hra': hra,
        'special': special,
        'company_name': 'Ace Bookkeeping Private Limited',
        'company_cin': 'U69201DC2026FTC469983',
        'company_address': '1/27, 1st Floor, Mall Road, Tilak Nagar (West Delhi), New Delhi, West Delhi - 110018, Delhi',
        'company_phone': '98110 08636',
        'company_email': 'info.acebookkeeping@gmail.com',
        'signatory': request.form.get('signatory_name', 'Ankit Kulshrestha'),
        'signatory_title': request.form.get('signatory_title', 'Director'),
    }

    if doc_type == 'offer':
        doc_data.update({
            'letter_date': to_ddmmyyyy(request.form.get('letter_date', date.today().isoformat())),
            'work_location': request.form.get('work_location', 'Office – Tilak Nagar, Delhi'),
            'start_date': to_ddmmyyyy(request.form.get('start_date', '')),
        })
    elif doc_type == 'agreement':
        doc_data.update({
            'address': request.form.get('employee_address', ''),
            'probation_period': request.form.get('probation_period', 'six (6) months'),
            'work_location': request.form.get('work_location', 'Office – Tilak Nagar, Delhi'),
            'hours_per_week': request.form.get('hours_per_week', '38'),
            'notice_period': request.form.get('notice_period', 'sixty (60) calendar days'),
        })
    elif doc_type == 'assets':
        assets_json = request.form.get('assets', '[]')
        try:
            assets = json.loads(assets_json)
        except:
            assets = []

        doc_data.update({
            'employment_type': request.form.get('employment_type', 'Full-Time'),
            'work_location': request.form.get('work_location', 'India'),
            'assets': assets
        })
    elif doc_type == 'increment':
        doc_data.update({
            'letter_date': to_ddmmyyyy(request.form.get('letter_date', date.today().isoformat())),
            'effective_from': to_ddmmyyyy(request.form.get('effective_from', date.today().isoformat())),
            'old_annual': request.form.get('old_annual', salary.monthly_salary * 12),
            'new_annual': request.form.get('new_annual', salary.monthly_salary * 12),
        })

    return render_template('onboarding_document_preview.html', doc=doc_data)

@app.route('/admin/assign-onboarding-document', methods=['POST'])
@login_required
@admin_required
def assign_onboarding_document():
    """Assign/Save onboarding document to employee"""
    employee_id = int(request.form.get('employee_id'))
    doc_type = request.form.get('doc_type')
    doc_title = request.form.get('doc_title')
    doc_html = request.form.get('doc_html')

    employee = User.query.get_or_404(employee_id)

    # Check if document already exists (don't create duplicates)
    existing_doc = OnboardingDocument.query.filter_by(
        user_id=employee_id,
        doc_type=doc_type
    ).first()

    if existing_doc:
        # Update existing document
        existing_doc.doc_title = doc_title
        existing_doc.doc_content = doc_html
        existing_doc.assigned_at = datetime.now()
        existing_doc.assigned_by = current_user.id
        existing_doc.status = 'assigned'
    else:
        # Create new document record
        onboarding_doc = OnboardingDocument(
            user_id=employee_id,
            doc_type=doc_type,
            doc_title=doc_title,
            doc_content=doc_html,
            assigned_by=current_user.id,
            status='assigned'
        )
        db.session.add(onboarding_doc)

    db.session.commit()
    flash(f'✅ {doc_title} assigned to {employee.username}!', 'success')
    return redirect(url_for('employee_onboarding'))


# Admin: HR Reports & Analytics
@app.route('/admin/hr-analytics')
@login_required
@admin_required
def hr_analytics():
    total_employees = User.query.count()
    total_departments = Department.query.count()
    today_present = Attendance.query.filter_by(date=datetime.now().date(), status='present').count()
    total_leaves = Leave.query.filter_by(status='approved').count()

    return render_template('hr_analytics.html',
        total_employees=total_employees,
        total_departments=total_departments,
        today_present=today_present,
        total_leaves=total_leaves
    )

# Holiday Calendar Routes
@app.route('/holidays')
@login_required
def view_holidays():
    """Display upcoming holidays"""
    holidays = Holiday.query.filter(Holiday.holiday_date >= datetime.now().date()).order_by(Holiday.holiday_date).all()
    all_holidays = Holiday.query.order_by(Holiday.holiday_date).all()
    return render_template('holidays_calendar.html', upcoming_holidays=holidays, all_holidays=all_holidays)

# Profile Routes
@app.route('/profile')
@login_required
def view_profile():
    """View employee profile"""
    user = current_user
    return render_template('profile.html', user=user)

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """Edit employee profile"""
    if request.method == 'POST':
        # Personal info
        current_user.phone = request.form.get('phone')
        current_user.address = request.form.get('address')
        current_user.city = request.form.get('city')
        current_user.state = request.form.get('state')
        current_user.postal_code = request.form.get('postal_code')
        current_user.country = request.form.get('country')

        # Personal details
        if request.form.get('date_of_birth'):
            current_user.date_of_birth = datetime.strptime(request.form.get('date_of_birth'), '%Y-%m-%d').date()
        current_user.gender = request.form.get('gender')
        current_user.blood_group = request.form.get('blood_group')

        # Emergency contact
        current_user.emergency_contact = request.form.get('emergency_contact')
        current_user.emergency_phone = request.form.get('emergency_phone')

        # Financial info
        current_user.pan_number = request.form.get('pan_number')
        current_user.aadhar_number = request.form.get('aadhar_number')
        current_user.bank_account = request.form.get('bank_account')
        current_user.ifsc_code = request.form.get('ifsc_code')

        # Employment (admin only)
        if current_user.role == 'admin':
            if request.form.get('date_of_joining'):
                current_user.date_of_joining = datetime.strptime(request.form.get('date_of_joining'), '%Y-%m-%d').date()
            current_user.designation = request.form.get('designation')
            current_user.reporting_manager = request.form.get('reporting_manager')

        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('view_profile'))

    return render_template('profile_edit.html', user=current_user)

@app.route('/my-documents')
@login_required
def my_documents():
    """View assigned onboarding documents"""
    documents = OnboardingDocument.query.filter_by(user_id=current_user.id)\
        .order_by(OnboardingDocument.assigned_at.desc()).all()

    return render_template('my_documents.html', documents=documents)

@app.route('/document/<int:doc_id>')
@login_required
def view_document(doc_id):
    """View a specific document"""
    doc = OnboardingDocument.query.get_or_404(doc_id)

    # Check if user has permission to view this document
    if doc.user_id != current_user.id and current_user.role != 'admin':
        flash('You do not have permission to view this document', 'error')
        return redirect(url_for('dashboard'))

    return render_template('view_document.html', document=doc)

@app.route('/document/<int:doc_id>/acknowledge', methods=['POST'])
@login_required
def acknowledge_document(doc_id):
    """Mark document as acknowledged"""
    doc = OnboardingDocument.query.get_or_404(doc_id)

    if doc.user_id != current_user.id:
        flash('You do not have permission to acknowledge this document', 'error')
        return redirect(url_for('dashboard'))

    doc.status = 'acknowledged'
    db.session.commit()
    flash('Document acknowledged successfully!', 'success')
    return redirect(url_for('my_documents'))

def init_db():
    with app.app_context():
        db.create_all()

        # Database migrations for existing databases
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)

            # Add lwp_used column to leave_balance if missing
            lb_columns = [col['name'] for col in inspector.get_columns('leave_balance')]
            if 'lwp_used' not in lb_columns:
                db.session.execute(text('ALTER TABLE leave_balance ADD COLUMN lwp_used FLOAT DEFAULT 0'))
                db.session.commit()

            # Add per-employee entitlement columns to leave_balance if missing
            if 'annual_accrued' not in lb_columns:
                db.session.execute(text('ALTER TABLE leave_balance ADD COLUMN annual_accrued FLOAT'))
                db.session.commit()
            if 'sick_accrued' not in lb_columns:
                db.session.execute(text('ALTER TABLE leave_balance ADD COLUMN sick_accrued FLOAT'))
                db.session.commit()
            if 'accrued_as_of' not in lb_columns:
                db.session.execute(text('ALTER TABLE leave_balance ADD COLUMN accrued_as_of DATE'))
                db.session.commit()

            # Add hours column to leave table if missing
            leave_columns = [col['name'] for col in inspector.get_columns('leave')]
            if 'hours' not in leave_columns:
                db.session.execute(text('ALTER TABLE leave ADD COLUMN hours FLOAT DEFAULT 0'))
                db.session.commit()

            # Add new columns to attendance table if missing
            try:
                att_columns = [col['name'] for col in inspector.get_columns('attendance')]
                if 'work_hours' not in att_columns:
                    db.session.execute(text('ALTER TABLE attendance ADD COLUMN work_hours FLOAT DEFAULT 0'))
                    db.session.commit()
                if 'productive_hours' not in att_columns:
                    db.session.execute(text('ALTER TABLE attendance ADD COLUMN productive_hours FLOAT DEFAULT 0'))
                    db.session.commit()
                if 'screen_time_hours' not in att_columns:
                    db.session.execute(text('ALTER TABLE attendance ADD COLUMN screen_time_hours FLOAT DEFAULT 0'))
                    db.session.commit()
                if 'source' not in att_columns:
                    db.session.execute(text("ALTER TABLE attendance ADD COLUMN source VARCHAR(50) DEFAULT 'manual'"))
                    db.session.commit()
            except Exception as e:
                print(f'Attendance table migration: {e}')

            # Add new columns to payment_invoice table if missing
            try:
                invoice_columns = [col['name'] for col in inspector.get_columns('payment_invoice')]

                if 'leave_deduction' not in invoice_columns:
                    db.session.execute(text('ALTER TABLE payment_invoice ADD COLUMN leave_deduction FLOAT DEFAULT 0'))
                    db.session.commit()
                    print('Added leave_deduction column to payment_invoice table')

                if 'manual_deduction' not in invoice_columns:
                    db.session.execute(text('ALTER TABLE payment_invoice ADD COLUMN manual_deduction FLOAT DEFAULT 0'))
                    db.session.commit()
                    print('Added manual_deduction column to payment_invoice table')
            except Exception as e:
                print(f'PaymentInvoice table migration: {e}')

            # Add new columns to training table if missing
            try:
                training_columns = [col['name'] for col in inspector.get_columns('training')]

                if 'html_content' not in training_columns:
                    db.session.execute(text('ALTER TABLE training ADD COLUMN html_content TEXT'))
                    db.session.commit()
                    print('Added html_content column to training table')

                if 'training_type' not in training_columns:
                    db.session.execute(text('ALTER TABLE training ADD COLUMN training_type VARCHAR(50) DEFAULT "video"'))
                    db.session.commit()
                    print('Added training_type column to training table')

                if 'video_url' not in training_columns:
                    db.session.execute(text('ALTER TABLE training ADD COLUMN video_url VARCHAR(500)'))
                    db.session.commit()
                    print('Added video_url column to training table')

                if 'icon' not in training_columns:
                    db.session.execute(text('ALTER TABLE training ADD COLUMN icon VARCHAR(50) DEFAULT "fa-graduation-cap"'))
                    db.session.commit()
                    print('Added icon column to training table')
            except Exception as e:
                print(f'Training table migration: {e}')
        except Exception:
            pass

        # Create default admin user if not exists
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(
                username='admin',
                email='admin@company.com',
                password=generate_password_hash('admin123'),
                role='admin',
                department='Administration'
            )
            db.session.add(admin)
            db.session.commit()

            balance = LeaveBalance(
                user_id=admin.id,
                year=datetime.now().year
            )
            db.session.add(balance)
            db.session.commit()
            print('Default admin user created: admin / admin123')

if __name__ == '__main__':
    init_db()
    # host='0.0.0.0' makes the app accessible to all users on the network
    app.run(host='0.0.0.0', port=5000, debug=True)
