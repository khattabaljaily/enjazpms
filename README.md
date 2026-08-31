# EnjazIMS — Inventory & Sales Management System

A multi-tenant SaaS system for inventory and sales management, initially targeting the Sudanese market with full Arabic (RTL) support.

---

## Tech Stack

| | |
|---|---|
| **Backend** | Django 4.2, Python 3.8+, MySQL |
| **Frontend** | Bootstrap 5 RTL, jQuery 3.7, DataTables 1.13.6, FontAwesome 6 |
| **AI** | DeepSeek API |
| **Font** | Cairo (Google Fonts) |

---

## Key Features

### Infrastructure
- Full data isolation between tenants via `TenantMixin`
- Three business setups: single store / multiple warehouses / multiple branches
- Subscription plans: trial / basic / professional / enterprise
- RBAC system: 26 sections, 145 permissions, split-panel UI for managing groups
- Activity log for every operation

### Core Modules
- **Customers & Suppliers** — CRUD + ledger + payments
- **Items** — hierarchical categories, units of measure with conversion factors, BOM for manufacturing, batch/lot tracking with expiry dates
- **Stocks** — opening balances, inter-warehouse transfers, stocktaking, manufacturing orders
- **Sales** — invoices, quotes, returns, POS, deferred delivery
- **Purchases** — invoices, RFQ (request for quotation), purchase orders, returns
- **Expenses** — categories + linked to treasuries
- **Treasury** — cash and bank accounts, inter-treasury transfers, hard-currency mode (price in USD/EUR, auto-reprice on exchange rate change)
- **Employees** — records, salary payments, instant advances, incentives/deductions, auto-linked to treasuries
- **Sales Agents** — accounts with commission based on invoice, collection, or both, plus a self-service portal for their invoices and statements

### Advanced Features
- **AI Assistant** — chat with your real data + automatic daily business insights (DeepSeek)
- **Online Store** — a public storefront per tenant (unique slug), cart, checkout, business-hours scheduling, printable QR code
- **Customer Portal** — magic-link access for customers to view their invoices and statements
- **Notifications** — automatic (low stock, overdue invoice, new order...) with smart analysis
- **Backups** — per-tenant, no mysqldump, 7-day retention
- **Support** — built-in ticketing system

### Reports (36+ reports)
Sales, purchases, inventory, expenses, treasury, income statement (P&L)

---

## Quick Start

```bash
# Activate the virtual environment
source .env/bin/activate

# Install requirements
pip install -r requirements.txt

# Apply migrations
python manage.py migrate

# Seed business types
python manage.py create_business_types

# Create the superuser
python manage.py createsuperuser

# Run the server
python manage.py runserver
```

---

## Project Structure

```
EnjazIMS/
├── PROJECT/          # Django settings
├── apps/
│   ├── core/         # Tenant, middleware, backup, support tickets
│   ├── accounts/     # User, RBAC, permission groups, activity log
│   ├── customers/    # Customers + customer portal
│   ├── suppliers/    # Suppliers
│   ├── items/        # Products, categories, units, BOM, batches
│   ├── stocks/       # Warehouses, transfers, stocktaking, manufacturing
│   ├── sales/        # Sales, quotes, POS, returns
│   ├── purchases/    # Purchases, RFQ, returns
│   ├── expenses/     # Expenses
│   ├── treasury/     # Treasuries, hard-currency mode
│   ├── employees/    # Payroll, advances, incentives
│   ├── agents/       # Sales agents, commissions, agent portal
│   ├── notifications/# Smart notifications
│   ├── ai/           # Chat + insights (DeepSeek)
│   ├── store/        # Public online store
│   └── portal/       # Customer portal
├── static/
│   ├── css/          # CSS files (main, layout, dashboard + per-feature)
│   └── js/
├── media/
└── manage.py
```

---

## Development Patterns

```python
# Every model inherits TenantMixin — automatic isolation
class Item(TenantMixin):
    name = models.CharField(max_length=300)

# Every sensitive view is permission-protected
@require_permission('add_sale_invoice')
def invoice_create(request): ...

# Every financial/stock mutation goes through services.py only
@transaction.atomic
def confirm_invoice(invoice): ...
```

```javascript
// AJAX only — no native location.reload() or alert()
$.ajax({ url: '/sales/api/', ... success: () => showToast('success', '...') });
```

---

## Platform Admin Dashboard

For superusers and platform staff:
- Tenant management (activate / suspend / renew)
- Revenue, subscription, and activity reports
- Support ticket management
- Audit log
- Backup management

---

**Version:** 1.0 — **Status:** Production — **Last updated:** August 2026
