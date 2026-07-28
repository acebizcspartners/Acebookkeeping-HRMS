# 🏢 Acebookking Leave Portal - Complete HRMS System

![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Python](https://img.shields.io/badge/python-3.8+-green)
![Flask](https://img.shields.io/badge/flask-2.0+-red)
![License](https://img.shields.io/badge/license-MIT-green)

A comprehensive **Human Resource Management System (HRMS)** built with Flask, featuring leave management, salary processing, attendance tracking, performance reviews, and complete employee management.

## 📋 Table of Contents

- [Features](#-features)
- [Technology Stack](#-technology-stack)
- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [Project Structure](#-project-structure)
- [Database Models](#-database-models)
- [API Routes](#-api-routes)
- [User Roles](#-user-roles)
- [Usage Guide](#-usage-guide)
- [Configuration](#-configuration)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

### 1. **Leave Management System** 📅
- Apply for leaves (Annual, Sick, LWP)
- Monthly leave accrual (9.2 hrs annual, 7.36 hrs sick)
- Leave balance tracking with hours-based calculation
- Admin approval/rejection workflow
- Leave revocation requests
- Leave transaction history
- Email notifications via n8n webhooks

### 2. **Attendance Management** 🕐
- Real-time check-in/check-out
- Automatic hours calculation
- Attendance status tracking (Present, Absent, Half-day, WFH)
- Daily attendance reports
- Attendance history with remarks

### 3. **Salary & Payroll** 💰
- Employee salary management
- Monthly salary configuration
- Hourly rate setup
- Payment invoice generation
- Deductions & bonus tracking
- Invoice status (Draft, Generated, Paid)
- Professional invoice display with print functionality

### 4. **Employee Management** 👥
- Comprehensive employee profiles
- Personal information storage
- Contact details & address
- Emergency contact management
- Employee documents (Resume, Certificates)
- Designation & department assignment
- Manager assignment
- Financial information (PAN, Aadhar, Bank Account)

### 5. **Performance Management** ⭐
- Performance reviews (1-5 rating scale)
- Individual skill assessment:
  - Technical Skills
  - Communication
  - Teamwork
- Goal setting & feedback
- Review period tracking
- Reviewer assignment

### 6. **Training & Development** 🎓
- Training program management
- Employee training enrollment
- Training dates & duration
- Trainer information & cost tracking
- Training status (Planned, Ongoing, Completed)
- Certificate issuance tracking

### 7. **HR Analytics Dashboard** 📊
- Employee statistics
- Department overview
- Attendance summaries
- Leave approval statistics
- Real-time HR metrics
- Quick action buttons

### 8. **Company Announcements** 📢
- Post company-wide announcements
- Announcement visibility control
- Employee notification center
- Archive old announcements

### 9. **User Authentication** 🔐
- Secure login/registration
- Password reset with OTP
- Role-based access control (Employee, Manager, Admin)
- Session management

### 10. **Admin Dashboard** 🎯
- Employee list management
- Salary management
- Invoice generation & tracking
- Leave approval workflows
- Performance review management
- Training program administration
- Department & designation management
- HR analytics

---

## 🛠 Technology Stack

### Backend
- **Framework:** Flask 2.0+
- **Database:** SQLite/PostgreSQL (via SQLAlchemy ORM)
- **Authentication:** Flask-Login
- **Security:** Werkzeug password hashing

### Frontend
- **HTML/CSS/JavaScript** (Responsive Design)
- **Bootstrap 5** - UI Framework
- **Font Awesome 6** - Icons
- **Google Fonts** - Typography

### Additional Services
- **Email:** SMTP (Gmail)
- **Webhooks:** n8n for email notifications
- **Database:** SQLAlchemy ORM

### Deployment
- **Platform:** Vercel (optional)
- **Database:** Neon PostgreSQL (optional)

---

## 📥 Installation

### Prerequisites
- Python 3.8 or higher
- pip (Python package manager)
- Git

### Step 1: Clone Repository
```bash
git clone https://github.com/rami629914-star/Acebiz-HRMS.git
cd Acebiz-HRMS
```

### Step 2: Create Virtual Environment
```bash
python -m venv venv

# On Windows:
venv\Scripts\activate

# On macOS/Linux:
source venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Set Environment Variables
Create a `.env` file in the project root:
```env
SECRET_KEY=your-secret-key-here
DATABASE_URL=sqlite:///leave_management.db
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
N8N_WEBHOOK_URL=your-n8n-webhook-url
```

### Step 5: Initialize Database
```bash
python app.py
```

The app will:
- Create database tables
- Create default admin user (username: `admin`, password: `admin123`)
- Initialize leave balances

---

## 🚀 Quick Start

### Running the Application
```bash
python app.py
```

Access the application:
- **URL:** http://localhost:5000
- **Admin Login:** admin / admin123

### First Steps
1. Login with admin account
2. Go to "Employee Salaries" to set employee salaries
3. Create salary records for employees
4. Generate payment invoices
5. Check "HR Analytics" dashboard

---

## 📁 Project Structure

```
Acebiz-HRMS/
├── app.py                          # Main Flask application
├── requirements.txt                # Python dependencies
├── README.md                       # This file
├── leave_management.db             # SQLite database
├── static/
│   ├── css/
│   │   └── style.css              # Main stylesheet
│   ├── images/
│   │   └── acebiz-logo.png        # Logo
│   └── js/
│       └── main.js                # JavaScript functions
└── templates/
    ├── base.html                  # Base template
    ├── login.html                 # Login page
    ├── register.html              # Registration page
    ├── dashboard.html             # Employee dashboard
    ├── attendance.html            # Attendance tracking
    ├── profile.html               # Employee profile
    ├── edit_profile.html          # Profile editing
    ├── my_salary.html             # Salary view
    ├── my_invoices.html           # Invoice list
    ├── view_invoice.html          # Invoice detail
    ├── admin_salaries.html        # Admin salary management
    ├── manage_salary.html         # Salary editing
    ├── generate_invoice.html      # Invoice generation
    ├── admin_invoices.html        # All invoices
    ├── leave_balance.html         # Leave balance view
    ├── my_leaves.html             # Leave applications
    ├── apply_leave.html           # Leave application form
    ├── manage_leaves.html         # Leave approval (admin)
    ├── leave_transactions.html    # Leave history
    ├── emergency_contacts.html    # Emergency contacts
    ├── performance_reviews.html   # Performance reviews (admin)
    ├── training.html              # Training programs (admin)
    ├── announcements.html         # Company announcements
    ├── hr_analytics.html          # HR dashboard (admin)
    ├── employees.html             # Employee list (admin)
    └── ...other templates
```

---

## 📊 Database Models

### User Model
```python
- id: Integer (Primary Key)
- username: String (Unique)
- email: String (Unique)
- password: String (Hashed)
- role: String (employee, manager, admin)
- department: String
- created_at: DateTime
```

### Leave Model
```python
- id: Integer (Primary Key)
- user_id: Foreign Key (User)
- leave_type: String (sick, annual, lwp)
- start_date: Date
- end_date: Date
- hours: Float
- reason: Text
- status: String (pending, approved, rejected, revoked)
- applied_on: DateTime
- reviewed_by: Foreign Key (User)
- reviewed_on: DateTime
- comments: Text
- revocation_requested: Boolean
- revocation_reason: Text
- revocation_requested_on: DateTime
```

### Salary Model
```python
- id: Integer (Primary Key)
- user_id: Foreign Key (User)
- monthly_salary: Float
- hourly_rate: Float
- currency: String (default: INR)
- effective_from: Date
- is_active: Boolean
- created_at: DateTime
- updated_at: DateTime
```

### PaymentInvoice Model
```python
- id: Integer (Primary Key)
- user_id: Foreign Key (User)
- month: Integer
- year: Integer
- salary_amount: Float
- deductions: Float
- bonus: Float
- net_amount: Float
- currency: String (default: INR)
- status: String (draft, generated, paid)
- invoice_number: String (Unique)
- generated_on: DateTime
- paid_on: DateTime (Nullable)
- notes: Text
```

### Attendance Model
```python
- id: Integer (Primary Key)
- user_id: Foreign Key (User)
- date: Date
- check_in: DateTime
- check_out: DateTime
- status: String (present, absent, half-day, wfh)
- hours_worked: Float
- remarks: Text
- created_at: DateTime
```

### EmployeeProfile Model
```python
- id: Integer (Primary Key)
- user_id: Foreign Key (User, Unique)
- phone: String
- address: Text
- city: String
- state: String
- postal_code: String
- country: String
- date_of_birth: Date
- gender: String
- designation_id: Foreign Key (Designation)
- department_id: Foreign Key (Department)
- joining_date: Date
- employment_type: String (Full-time, Part-time, Contract)
- manager_id: Foreign Key (User)
- blood_group: String
- pan_number: String
- aadhar_number: String
- bank_account: String
- ifsc_code: String
- created_at: DateTime
- updated_at: DateTime
```

### PerformanceReview Model
```python
- id: Integer (Primary Key)
- user_id: Foreign Key (User)
- reviewer_id: Foreign Key (User)
- review_date: Date
- period_start: Date
- period_end: Date
- performance_rating: Float (1-5)
- technical_skills: Float (1-5)
- communication: Float (1-5)
- teamwork: Float (1-5)
- comments: Text
- goals: Text
- created_at: DateTime
```

### Training Model
```python
- id: Integer (Primary Key)
- title: String
- description: Text
- start_date: Date
- end_date: Date
- trainer: String
- cost: Float
- status: String (planned, ongoing, completed)
- created_at: DateTime
```

### Additional Models
- **Department:** Organization structure
- **Designation:** Job roles
- **EmergencyContact:** Employee emergency contacts
- **EmployeeDocument:** Employee documents storage
- **EmployeeTraining:** Employee training enrollment
- **EmployeeAsset:** Company assets tracking
- **Announcement:** Company announcements
- **LeaveBalance:** Leave tracking
- **LeaveTransaction:** Leave audit trail

---

## 🛣 API Routes

### Authentication Routes
```
POST   /register              - User registration
POST   /login                 - User login
POST   /logout                - User logout
POST   /forgot-password       - Request password reset
POST   /verify-otp            - Verify OTP
POST   /reset-password        - Reset password
POST   /resend-otp            - Resend OTP
```

### Dashboard & Profile Routes
```
GET    /                      - Home redirect
GET    /dashboard             - User dashboard
GET    /profile               - View profile
GET    /profile/edit          - Edit profile form
POST   /profile/edit          - Save profile changes
```

### Leave Management Routes
```
GET    /apply-leave           - Leave application form
POST   /apply-leave           - Submit leave application
GET    /my-leaves             - View user's leaves
GET    /leave-balance         - View leave balance
GET    /leave-transactions    - View leave history
POST   /leave/<id>/cancel     - Cancel pending leave
POST   /leave/<id>/request-revocation - Request revocation
GET    /manage-leaves         - Admin: Manage leaves
POST   /leave/<id>/approve    - Admin: Approve leave
POST   /leave/<id>/reject     - Admin: Reject leave
POST   /leave/<id>/approve-revocation - Admin: Approve revocation
POST   /leave/<id>/reject-revocation  - Admin: Reject revocation
```

### Attendance Routes
```
GET    /attendance            - View attendance
POST   /attendance/checkin    - Check in
POST   /attendance/checkout   - Check out
```

### Salary & Invoice Routes
```
GET    /my-salary             - View own salary
GET    /admin/salaries        - Admin: View all salaries
GET    /employee/<id>/salary  - Admin: Manage employee salary
POST   /employee/<id>/salary  - Admin: Save salary
GET    /invoice/generate/<id> - Admin: Invoice generation form
POST   /invoice/generate/<id> - Admin: Generate invoice
GET    /invoice/<id>          - View invoice
GET    /invoices              - Employee: View own invoices
GET    /admin/invoices        - Admin: View all invoices
POST   /invoice/<id>/mark-paid - Admin: Mark invoice as paid
```

### Employee Management Routes
```
GET    /employees             - Admin: Employee list
POST   /employee/<id>/update-role - Admin: Change employee role
```

### Performance Review Routes
```
GET    /admin/performance-reviews - Admin: View reviews
POST   /admin/performance-review/add - Admin: Add review
```

### Training Routes
```
GET    /admin/training        - Admin: View training programs
POST   /admin/training/add    - Admin: Add training program
```

### HR Analytics Routes
```
GET    /admin/hr-analytics    - Admin: HR dashboard
```

### Other Routes
```
GET    /emergency-contacts    - View emergency contacts
POST   /emergency-contact/add - Add emergency contact
POST   /emergency-contact/<id>/delete - Delete emergency contact
GET    /announcements         - View announcements
POST   /admin/announcement/add - Admin: Post announcement
GET    /api/leave-stats       - API: Get leave statistics
```

---

## 👥 User Roles

### Employee
- Apply for leaves
- View own leave balance & history
- View own salary & invoices
- Manage emergency contacts
- View personal profile
- Check attendance
- View announcements

### Manager
- All Employee permissions PLUS:
- Approve/reject leave requests
- Approve leave revocations
- View team's leave requests
- Access HR analytics

### Admin
- All Manager permissions PLUS:
- Manage employees
- Set employee salaries
- Generate payment invoices
- Mark invoices as paid
- Add performance reviews
- Create training programs
- Post announcements
- Manage departments & designations
- Full HR system control

---

## 📖 Usage Guide

### Employee: Applying for Leave
1. Go to **Dashboard**
2. Click **"Apply Leave"**
3. Select leave type (Annual, Sick, LWP)
4. Choose start & end dates
5. Enter hours & reason
6. Submit application
7. Receive notification when approved/rejected

### Employee: Viewing Salary
1. Click **"Salary"** in navigation
2. View monthly salary & hourly rate
3. Click **"My Invoices"** to see payment invoices
4. Click invoice to view details or print

### Employee: Attendance
1. Click **"Attendance"** in navigation
2. Click **"Check In"** to log arrival time
3. Click **"Check Out"** to log departure time
4. View attendance history

### Admin: Setting Employee Salary
1. Go to **"Salaries"** (for admin)
2. Find employee and click **"Salary"**
3. Enter monthly salary & hourly rate
4. Set effective date
5. Save changes

### Admin: Generating Invoice
1. Go to **"Salaries"**
2. Click **"Invoice"** button for employee
3. Select month & year
4. Add bonuses/deductions (optional)
5. Click **"Generate Invoice"**
6. View & print invoice

### Admin: Managing Leaves
1. Go to **"Manage Leaves"**
2. Filter by status (Pending, Approved, Rejected)
3. Click employee's leave to view details
4. Approve or reject with comments
5. System sends notification to employee

### Admin: Adding Performance Review
1. Go to **"Reviews"** (Admin menu)
2. Click **"Add Review"**
3. Select employee & review period
4. Rate skills (1-5 scale):
   - Technical Skills
   - Communication
   - Teamwork
5. Add comments & goals
6. Submit review

### Admin: Creating Training
1. Go to **"Training"** (Admin menu)
2. Click **"Add Training"**
3. Enter training details
4. Set dates & trainer info
5. Add cost information
6. Submit

---

## ⚙️ Configuration

### Email Configuration
Update in `app.py`:
```python
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = 'your-email@gmail.com'
app.config['MAIL_PASSWORD'] = 'your-app-password'
```

### Database Configuration
Set `DATABASE_URL` environment variable:
- **SQLite:** `sqlite:///leave_management.db`
- **PostgreSQL:** `postgresql://user:password@localhost/hrms`

### n8n Webhooks
For email notifications, set:
```env
N8N_WEBHOOK_URL=https://your-n8n-instance.com/webhook/leaves
```

### Leave Accrual Settings
Edit constants in `app.py`:
```python
ANNUAL_LEAVE_MONTHLY_CREDIT = 9.2    # hours per month
SICK_LEAVE_MONTHLY_CREDIT = 7.36     # hours per month
```

---

## 🔐 Security Features

- ✅ Password hashing with Werkzeug
- ✅ CSRF protection on forms
- ✅ SQL injection prevention via ORM
- ✅ Role-based access control
- ✅ Session management
- ✅ OTP-based password reset
- ✅ Secure cookie handling

---

## 📱 Responsive Design

- ✅ Mobile-friendly interface
- ✅ Hamburger menu on mobile
- ✅ Responsive navigation
- ✅ Touch-friendly buttons
- ✅ Optimized for all screen sizes

---

## 🐛 Troubleshooting

### Issue: "Database locked" error
**Solution:** Close other instances of the app and try again

### Issue: Email notifications not sending
**Solution:** 
1. Check Gmail app password
2. Enable "Less secure apps" (if using Gmail)
3. Verify n8n webhook URL
4. Check internet connection

### Issue: Admin login not working
**Solution:** Delete `leave_management.db` and restart app

### Issue: Leave balance shows incorrect hours
**Solution:** Clear `LeaveBalance` records and restart the app

---

## 📊 Database Backup

### Backup SQLite Database
```bash
cp leave_management.db leave_management.db.backup
```

### Backup PostgreSQL Database
```bash
pg_dump hrms_db > backup.sql
```

---

## 🚀 Deployment

### Deploy to Vercel
1. Create `vercel.json`:
```json
{
  "builds": [{"src": "app.py", "use": "@vercel/python"}],
  "routes": [{"src": "/(.*)", "dest": "app.py"}]
}
```

2. Push to GitHub
3. Connect to Vercel
4. Add environment variables
5. Deploy

### Use Neon PostgreSQL
- Create account at neon.tech
- Get connection string
- Set as `DATABASE_URL`

---

## 📝 Requirements

See `requirements.txt`:
```
Flask==2.3.0
Flask-SQLAlchemy==3.0.0
Flask-Login==0.6.2
Werkzeug==2.3.0
requests==2.31.0
```

---

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

---

## 📄 License

This project is licensed under the MIT License - see LICENSE file for details

---

## 👨‍💻 Author

**Acebiz Consultants**
- Website: [Acebiz](https://acebiz.com.au)
- Email: rami629914@gmail.com

---

## 📞 Support

For support, email: support@acebiz.com.au

---

## 🎯 Roadmap

### Version 2.0 (Current) ✅
- ✅ Leave Management
- ✅ Salary & Invoices
- ✅ Attendance Tracking
- ✅ Performance Reviews
- ✅ Training Management
- ✅ HR Analytics

### Version 3.0 (Planned)
- 📋 Mobile App (React Native)
- 📊 Advanced Analytics & Reports
- 🤖 AI-based performance prediction
- 🔔 Push Notifications
- 📈 Payroll automation
- 🌐 Multi-language support
- 📱 PWA support

---

## 🙏 Acknowledgments

- Flask framework community
- SQLAlchemy ORM
- Bootstrap CSS framework
- Font Awesome icons
- n8n for automation

---

**Made with ❤️ by Acebiz Consultants**

Last Updated: 2026-07-28
Version: 2.0.0
