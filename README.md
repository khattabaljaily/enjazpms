# EnjazIMS - Multi-Tenant Inventory Management System

**Enterprise-grade inventory and point-of-sale system with multi-tenant architecture**

---

## 🎯 Overview

EnjazIMS is a comprehensive inventory management and POS system designed to support multiple businesses (multi-tenant) across various industries:
- 💊 Pharmacies
- 🛒 Supermarkets
- 🍕 Restaurants
- 👔 Apparel Stores
- 📱 Electronics Shops
- And more...

Built with Django 4.2, the system features complete tenant isolation, modern RTL-first UI, and a modular architecture for scalability.

---

## ✨ Key Features

### 🏢 Multi-Tenant Architecture
- **Complete data isolation** between tenants using tenant-scoped models
- **Custom settings** per business type with flexible configuration
- **Multi-branch support** with separate warehouses and locations
- **Tenant middleware** for automatic filtering and security
- **Subscription management** with expiry tracking

### 🎨 Modern UI Design System
- **Tailwind-inspired layout** with centered containers (max-width: 1600px)
- **Dark/Light mode** with CSS variables and smooth transitions
- **RTL-first design** with Cairo font family (4 weights)
- **Modular CSS architecture**:
  - `main.css` - Fonts & CSS variables
  - `layout.css` - Navbar, sidebar, dashboard structure
  - `dashboard.css` - Widgets, KPI cards, charts
  - `customers.css` - Customer management UI
- **Responsive design** with mobile-first approach
- **Modern components**: KPI cards, circular charts, pipeline visualization, quick actions

### ⚡ High Performance
- **AJAX-powered** - No full page reloads (enforced pattern)
- **REST API ready** with Django REST Framework
- **DataTables** for advanced table features
- **Optimized queries** with select_related/prefetch_related
- **CDN assets** for Bootstrap, FontAwesome, jQuery

### 🔐 Security & Access Control
- **Tenant-scoped authentication** with custom User model
- **Role-based permissions** per tenant
- **Middleware protection** for all tenant operations
- **Session management** with secure defaults

---

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Virtual environment
- MySQL/PostgreSQL (or SQLite for development)

### Installation

```bash
# 1. Activate virtual environment
source .env/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Apply migrations
python manage.py migrate

# 4. Create business types
python manage.py create_business_types

# 5. Create superuser
python manage.py createsuperuser

# 6. Run development server
python manage.py runserver
```

Visit `http://localhost:8000` to access the system.

---

## 📚 Documentation

- **[QUICK_START.md](docs/QUICK_START.md)** - Quick start guide
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Technical architecture
- **[DESIGN_GUIDE.md](docs/DESIGN_GUIDE.md)** - Design system guide
- **[DEVELOPMENT_STRATEGY.md](docs/DEVELOPMENT_STRATEGY.md)** - Development strategy
- **[LOGO_USAGE_GUIDE.md](docs/LOGO_USAGE_GUIDE.md)** - Brand logo guidelines

---

## 📦 Project Structure

```
EnjazIMS/
├── PROJECT/                  # Django settings & configuration
│   ├── settings.py          # Main settings with tenant support
│   ├── urls.py              # Root URL configuration
│   └── wsgi.py              # WSGI application
├── apps/                     # Django apps (modular architecture)
│   ├── core/                # Core tenant functionality
│   │   ├── models.py        # Tenant, BusinessType, TenantSettings
│   │   ├── middleware.py    # TenantMiddleware for tenant resolution
│   │   ├── context_processors.py  # Global template context
│   │   └── templates/       # Dashboard, settings, subscription views
│   ├── accounts/            # User authentication & profiles
│   │   ├── models.py        # CustomUser with tenant relationship
│   │   ├── forms.py         # Multi-step registration
│   │   └── templates/       # Login, register, profile
│   └── customers/           # Customer management
│       ├── models.py        # Customer model with tenant scope
│       ├── views.py         # CRUD operations
│       └── templates/       # Modern card-based customer UI
├── static/                   # Static assets
│   ├── css/                 # Stylesheets
│   │   ├── main.css         # Fonts & CSS variables (201 lines)
│   │   ├── layout.css       # Layout components (300+ lines)
│   │   ├── dashboard.css    # Dashboard widgets (500+ lines)
│   │   └── customers.css    # Customer pages (500+ lines)
│   ├── js/
│   │   └── main.js          # Global JavaScript utilities
│   ├── fonts/
│   │   └── cairo/           # Cairo font family files
│   └── img/
│       └── logo/            # Brand logo variants
├── docs/                     # Project documentation
├── media/                    # User-uploaded files
├── requirements.txt          # Python dependencies
├── manage.py                # Django management script
└── README.md                # This file
```

---

## 🛠️ Technology Stack

### Backend
- **Django 4.2.7** - Web framework
- **Python 3.8+** - Programming language
- **MySQL** - Primary database
- **Django ORM** - Database abstraction

### Frontend
- **Bootstrap 5 RTL** - UI framework (CDN)
- **Cairo Font** - Arabic typography (400, 500, 600, 700 weights)
- **jQuery 3.7** - DOM manipulation & AJAX
- **DataTables 1.13.6** - Advanced tables
- **FontAwesome 6** - Icon library
- **CSS Grid & Flexbox** - Modern layouts

### Architecture Patterns
- **Multi-Tenant SaaS** - Shared database with tenant isolation
- **MVT Pattern** - Django's Model-View-Template
- **Modular Apps** - Self-contained Django applications
- **AJAX-first** - No full page reloads (mandatory pattern)

---

## 👥 Multi-Tenancy System

### Tenant Isolation
Each tenant (business) has completely isolated:
- ✅ Users and administrators
- ✅ Branches and warehouses
- ✅ Products, customers, and suppliers
- ✅ Invoices and transactions
- ✅ Custom settings and permissions

### Implementation

```python
# Base tenant-aware model mixin
class TenantMixin(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    
    objects = TenantManager()  # Auto-filters by current tenant
    
    class Meta:
        abstract = True

# Example usage
class Customer(TenantMixin):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    # Automatically scoped to current tenant
```

### Tenant Middleware
- Resolves tenant from subdomain or session
- Injects tenant into request object
- Protects all tenant-scoped views
- Handles subscription expiry

---

## 📊 Current Status

### ✅ Completed Features
- [x] Multi-tenant infrastructure (Tenant, BusinessType models)
- [x] Custom User model with tenant relationship
- [x] Tenant middleware and context processors
- [x] Complete CSS architecture (4 files, 1500+ lines)
- [x] Modern dashboard with KPI cards and charts
- [x] Customer management module (CRUD with AJAX)
- [x] Authentication system (login, register, profile)
- [x] Subscription management
- [x] Business types seed data
- [x] Dark/Light theme toggle
- [x] RTL layout with proper Arabic support

### 🚧 In Development
- [ ] Sales & POS module
- [ ] Inventory management
- [ ] Purchase orders
- [ ] Supplier management
- [ ] Product catalog
- [ ] Reporting & analytics
- [ ] Branch management
- [ ] Stock transfers

---

## 🎨 UI Components

### Dashboard Features
- **KPI Cards**: 4-card grid showing key metrics (users, customers, products, suppliers)
- **Circular Charts**: Progress indicators with percentage display
- **Pipeline Visualization**: 5-stage sales pipeline (Lead → Quote → Order → Invoice → Paid)
- **Quick Actions**: Common tasks with icon buttons
- **Category List**: Product category breakdown
- **Status Indicators**: Color-coded status badges
- **Welcome Card**: Personalized greeting

### Layout Structure
- **Fixed Navbar**: 70px height with logo, theme toggle, tenant menu, user menu
- **Sidebar**: 260px width, right-aligned (RTL), scrollable navigation
- **Main Content**: Flex-grow container with rounded background
- **Centered Container**: max-width 1600px for professional layout

---

## 📋 Development Rules

### ❌ Prohibited Patterns
```javascript
alert('...')              // ❌ Never use native alerts
confirm('...')            // ❌ Never use native confirms
prompt('...')             // ❌ Never use native prompts
location.reload()         // ❌ Never reload the full page
```

### ✅ Required Patterns
```javascript
// Use toast notifications
showToast('success', 'Saved successfully');

// Use AJAX for all operations
$.ajax({
    url: '/api/customers/',
    method: 'POST',
    data: formData,
    success: function(response) {
        // Update UI without reload
    }
});

// Use modular JavaScript
// Each app has its own JS file
```

### CSS Guidelines
- Use CSS variables for theming (`--primary`, `--bg-secondary`, etc.)
- Follow BEM naming for custom classes
- Prefer CSS Grid/Flexbox over floats
- Keep specificity low
- Mobile-first media queries

---

## 🚀 Deployment

### Production Checklist
- [ ] Set `DEBUG = False` in settings
- [ ] Configure proper database (MySQL/PostgreSQL)
- [ ] Set up static files serving (WhiteNoise or CDN)
- [ ] Configure ALLOWED_HOSTS
- [ ] Set secure SECRET_KEY
- [ ] Enable HTTPS
- [ ] Configure email backend
- [ ] Set up backup strategy
- [ ] Configure logging
- [ ] Run collectstatic

---

## 📝 Contributing

To contribute to EnjazIMS:
1. Read the [ARCHITECTURE.md](docs/ARCHITECTURE.md) for technical overview
2. Review [DESIGN_GUIDE.md](docs/DESIGN_GUIDE.md) for UI patterns
3. Follow the AJAX-first development pattern
4. Keep apps modular and self-contained
5. Write tests for new features
6. Use meaningful commit messages

---

## 📄 License

Proprietary - All rights reserved

---

**Last Updated:** April 28, 2026  
**Version:** 1.0  
**Status:** Active Development

---

## 📚 الوثائق المرجعية

جميع الوثائق التحليلية والتصميمية متوفرة في:
- `/Users/khattab/projects/ims/docs/`

تتضمن:
1. تحليل شامل للمشروع الحالي
2. متطلبات النظام الجديد
3. تحسينات قاعدة البيانات
4. دليل AJAX والإشعارات
5. نمط التصميم الموحّد
6. نظام Modals والاستيراد/التصدير
7. دليل البدء السريع

---

## ✅ الخطة القادمة

### المرحلة 1: Setup (يومان)
- [x] إنشاء المشروع والبيئة
- [ ] إنشاء مشروع Django
- [ ] إعداد Settings
- [ ] إنشاء التطبيقات الأساسية

### المرحلة 2: Core (أسبوع)
- [ ] نظام المستخدمين والصلاحيات
- [ ] Models الأساسية
- [ ] Base Templates
- [ ] CSS System

### المرحلة 3: Features (2-3 أسابيع)
- [ ] إدارة المنتجات
- [ ] إدارة العملاء
- [ ] إدارة الموردين
- [ ] المبيعات والمشتريات

### المرحلة 4: Advanced (2 أسبوع)
- [ ] التقارير والإحصائيات
- [ ] تصدير واستيراد
- [ ] Dashboard متقدم

---

**تاريخ الإنشاء:** 27 أبريل 2026  
**الحالة:** 🚧 قيد التطوير  
**الإصدار:** 1.0.0-alpha
