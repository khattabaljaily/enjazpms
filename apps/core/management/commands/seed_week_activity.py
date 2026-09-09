# -*- coding: utf-8 -*-
"""
seed_week_activity — يضيف كماً كبيراً من الحركات (مبيعات، مشتريات، مصروفات، طلبات متجر...)
على مشترك موجود بالفعل، بتواريخ موزّعة على آخر N يوم (افتراضياً 7)، دون حذف أي بيانات حالية.

الاستخدام:
    python manage.py seed_week_activity
    python manage.py seed_week_activity --tenant صيدلية-Snake --days 7
"""
import random
from datetime import date, datetime, timedelta, time as dtime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import transaction

User = get_user_model()


# (name, category_name, cost, sell, batch_prefix)
NEW_ITEMS = [
    ('فيتامين سي 1000 مجم فوّار',      'فيتامينات ومكملات',   4500,   8000,  'VITC'),
    ('فيتامين د3 5000 وحدة',            'فيتامينات ومكملات',   5200,   9500,  'VITD'),
    ('أوميغا 3 كبسولات',                'فيتامينات ومكملات',   8000,  14000,  'OMG3'),
    ('زنك + فيتامينات مناعة',           'فيتامينات ومكملات',   3800,   7200,  'ZINC'),
    ('شراب سعال للأطفال',                'أدوية',               3200,   6500,  'CUFS'),
    ('مرهم مضاد حيوي موضعي',            'أدوية',               2800,   5500,  'OINT'),
    ('قطرة عين مطهرة',                   'أدوية',               3500,   7000,  'EYED'),
    ('محلول ملحي للاستنشاق',            'أدوية',               1800,   3500,  'SALN'),
    ('كريم مرطب للبشرة',                 'العناية بالبشرة',     6000,  11000,  'MOIS'),
    ('غسول فموي مطهر',                   'العناية بالبشرة',     4200,   8500,  'MWSH'),
    ('واقي شمسي SPF50',                  'العناية بالبشرة',     7500,  13500,  'SPF5'),
    ('جهاز قياس السكر الرقمي',           'منتجات اخرى',        65000, 105000,  'GLUC'),
    ('حفاضات أطفال — عبوة اقتصادية',    'منتجات اخرى',        18000,  32000,  'DIAP'),
    ('معقم يدين 500 مل',                 'منتجات اخرى',         5500,  10000,  'SANI'),
]

NEW_CUSTOMERS = [
    ('صيدلية الرحمة الفرعية', '0912001122', 'الخرطوم بحري', 500000),
    ('مركز النور الطبي',       '0922002233', 'أمدرمان',       800000),
    ('هبة الله عثمان',         '0933003344', 'الخرطوم',            0),
    ('عائشة الطيب محمد',       '0944004455', 'الخرطوم',            0),
    ('مصطفى الحسن آدم',        '0955005566', 'أمدرمان',            0),
    ('عيادة السلامة العامة',   '0966006677', 'بحري',           300000),
    ('نور الدين إبراهيم',      '0977007788', 'الخرطوم',            0),
    ('سارة كمال الدين',        '0988008899', 'الخرطوم',            0),
]

NEW_SUPPLIERS = [
    ('الشركة السودانية للأدوية',  '0911112233', 'الخرطوم'),
    ('مجموعة النيل للمستلزمات الطبية', '0922223344', 'أمدرمان'),
]

EXPENSE_CATEGORIES = ['إيجار', 'كهرباء وماء', 'مواصلات ونقل', 'صيانة', 'رواتب ومكافآت', 'متنوع']

EMPLOYEES = [
    ('د. إسلام عبد الرحيم', 'صيدلي مسؤول', 'الصيدلة', 'fixed', 350000),
    ('محمد وليد كرم',        'كاشير',        'المبيعات', 'fixed', 180000),
    ('آدم عثمان بابكر',       'عامل مخزن',    'المخزن',   'fixed', 160000),
]


class Command(BaseCommand):
    help = 'يضيف بيانات نشاط غنية (مبيعات/مشتريات/مصروفات...) على مشترك موجود بتواريخ هذا الأسبوع'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default=None, help='slug المشترك (اختياري إن كان هناك مشترك واحد فقط)')
        parser.add_argument('--days', type=int, default=7, help='عدد الأيام الماضية لتوزيع الحركات عليها')

    def handle(self, *args, **options):
        from apps.core.models import Tenant

        tenant = self._resolve_tenant(Tenant, options['tenant'])
        self.days = options['days']
        self.today = date.today()

        user = self._resolve_user(tenant)
        stock = self._resolve_stock(tenant)
        cash_treasury = self._resolve_cash_treasury(tenant)

        self.stdout.write(f'⏳ إضافة نشاط تجريبي على «{tenant.name}» لآخر {self.days} يوم...')

        with transaction.atomic():
            categories = self._ensure_categories(tenant, user)
            items = self._ensure_items(tenant, user, categories)
            customers = self._ensure_customers(tenant, user)
            suppliers = self._ensure_suppliers(tenant, user)
            self._create_purchases(tenant, user, stock, suppliers, items)
            self._create_sales(tenant, user, stock, customers, items)
            self._create_expenses(tenant, user, cash_treasury)
            self._create_employees(tenant, user, cash_treasury)
            self._create_store_orders(tenant, items)
            self._create_notifications(tenant, user, items, customers)

        self.stdout.write(self.style.SUCCESS('✅ اكتمل! افتح لوحة التحكم لمشاهدة النشاط.'))

    # ── helpers ────────────────────────────────────────────────────────────
    def _resolve_tenant(self, Tenant, slug):
        if slug:
            try:
                return Tenant.objects.get(slug=slug)
            except Tenant.DoesNotExist:
                raise CommandError(f'لا يوجد مشترك بـ slug={slug}')
        qs = Tenant.objects.all()
        if qs.count() != 1:
            raise CommandError('يوجد أكثر من مشترك — حدد --tenant <slug>')
        return qs.first()

    def _resolve_user(self, tenant):
        user = User.objects.filter(tenant=tenant, is_tenant_admin=True, is_active=True).first()
        if not user:
            user = User.objects.filter(tenant=tenant, is_active=True).first()
        if not user:
            raise CommandError('لا يوجد مستخدم نشط لهذا المشترك')
        return user

    def _resolve_stock(self, tenant):
        from apps.stocks.models import Stock
        stock = Stock.objects.filter(tenant=tenant, is_default=True).first() \
            or Stock.objects.filter(tenant=tenant).first()
        if not stock:
            raise CommandError('لا يوجد مخزن لهذا المشترك')
        return stock

    def _resolve_cash_treasury(self, tenant):
        from apps.treasury.models import Treasury
        t = Treasury.objects.filter(tenant=tenant, is_default=True).first() \
            or Treasury.objects.filter(tenant=tenant).first()
        if not t:
            raise CommandError('لا توجد خزينة لهذا المشترك')
        return t

    def _spread_day(self, i, total):
        """يوزّع i من أصل total على آخر self.days يوماً (0..days-1) مع بعض العشوائية."""
        days_ago = int(i * self.days / max(total, 1))
        days_ago = min(days_ago, self.days - 1)
        return self.today - timedelta(days=days_ago)

    def _qty_for_value(self, price, low, high, max_qty):
        """كمية تجعل قيمة السطر تقارب مبلغاً مستهدفاً، بدل كمية ثابتة تتجاهل سعر الصنف."""
        price = float(price) or 1.0
        target = random.uniform(low, high)
        qty = max(1, round(target / price))
        return Decimal(str(min(qty, max_qty)))

    # ── Categories ─────────────────────────────────────────────────────────
    def _ensure_categories(self, tenant, user):
        from apps.items.models import Category
        names = ['أدوية', 'منتجات اخرى', 'فيتامينات ومكملات', 'العناية بالبشرة']
        cats = {}
        for n in names:
            cat, _ = Category.objects.get_or_create(
                tenant=tenant, name=n, defaults=dict(is_active=True, created_by=user),
            )
            cats[n] = cat
        self.stdout.write(f'  ✓ تصنيفات: {len(cats)}')
        return cats

    # ── Items ──────────────────────────────────────────────────────────────
    def _ensure_items(self, tenant, user, categories):
        from apps.items.models import Item
        items = list(Item.objects.filter(tenant=tenant))
        existing_names = {i.name for i in items}
        created = 0
        for idx, (name, cat_name, cost, sell, prefix) in enumerate(NEW_ITEMS):
            if name in existing_names:
                continue
            item = Item.objects.create(
                tenant=tenant, name=name, category=categories[cat_name],
                item_type='product', cost_price=Decimal(str(cost)),
                selling_price=Decimal(str(sell)), sku=f'{prefix}-{idx+1:03d}',
                is_active=True, is_sellable=True, is_purchasable=True,
                created_by=user,
            )
            items.append(item)
            created += 1
        self.stdout.write(f'  ✓ أصناف جديدة: {created} (إجمالي {len(items)})')
        return items

    # ── Customers ──────────────────────────────────────────────────────────
    def _ensure_customers(self, tenant, user):
        from apps.customers.models import Customer
        customers = list(Customer.objects.filter(tenant=tenant))
        existing_names = {c.name for c in customers}
        created = 0
        for name, phone, city, cl in NEW_CUSTOMERS:
            if name in existing_names:
                continue
            c = Customer.objects.create(
                tenant=tenant, name=name, phone=phone, city=city,
                credit_limit=Decimal(str(cl)), created_by=user,
            )
            customers.append(c)
            created += 1
        self.stdout.write(f'  ✓ عملاء جدد: {created} (إجمالي {len(customers)})')
        return customers

    # ── Suppliers ──────────────────────────────────────────────────────────
    def _ensure_suppliers(self, tenant, user):
        from apps.suppliers.models import Supplier
        suppliers = list(Supplier.objects.filter(tenant=tenant))
        existing_names = {s.name for s in suppliers}
        created = 0
        for name, phone, city in NEW_SUPPLIERS:
            if name in existing_names:
                continue
            s = Supplier.objects.create(tenant=tenant, name=name, phone=phone, city=city, created_by=user)
            suppliers.append(s)
            created += 1
        self.stdout.write(f'  ✓ موردون جدد: {created} (إجمالي {len(suppliers)})')
        return suppliers

    # ── Purchases ──────────────────────────────────────────────────────────
    def _create_purchases(self, tenant, user, stock, suppliers, items):
        from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine
        from apps.purchases.services import confirm_purchase_invoice

        n_invoices = 14
        # الدفع البنكي/الآجل لا يستهلكان رصيد الخزينة النقدية — الأنسب لأوامر شراء بقيم كبيرة
        payment_cycle = ['bank', 'credit', 'bank', 'credit', 'bank', 'credit', 'cash']
        confirmed = 0
        for i in range(n_invoices):
            inv_date = self._spread_day(i, n_invoices)
            supplier = suppliers[i % len(suppliers)]
            pm = payment_cycle[i % len(payment_cycle)]
            line_items = random.sample(items, k=min(4, len(items)))

            invoice = PurchaseInvoice(
                tenant=tenant, supplier=supplier, stock=stock,
                invoice_date=inv_date, status='draft', payment_method=pm,
                created_by=user,
            )
            if pm == 'bank':
                invoice.bank_reference = f'TRF-{inv_date.strftime("%y%m%d")}-{i+1:02d}'
            invoice.save()

            subtotal = Decimal('0')
            expiry = inv_date + timedelta(days=random.randint(365, 730))
            for item in line_items:
                cost = item.cost_price or Decimal('10')
                # كمية شراء تستهدف قيمة سطر بين 20,000 و120,000 بدل رقم ثابت يتجاهل سعر الصنف
                qty = self._qty_for_value(cost, 20000, 120000, max_qty=200)
                line_total = cost * qty
                subtotal += line_total
                PurchaseInvoiceLine.objects.create(
                    tenant=tenant, invoice=invoice, item=item, quantity=qty,
                    unit_cost=cost, line_total=line_total,
                    batch_number=f'{item.sku or "BATCH"}-{inv_date.strftime("%y%m%d")}',
                    expiry_date=expiry, created_by=user,
                )
            invoice.subtotal = subtotal
            invoice.grand_total = subtotal
            invoice.save(update_fields=['subtotal', 'grand_total'])
            try:
                confirm_purchase_invoice(invoice, user)
                confirmed += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'    ⚠ شراء ({inv_date}): {e}'))

        self.stdout.write(f'  ✓ فواتير شراء: {confirmed}/{n_invoices}')

    # ── Sales ──────────────────────────────────────────────────────────────
    def _create_sales(self, tenant, user, stock, customers, items):
        from apps.sales.models import SaleInvoice, SaleInvoiceLine
        from apps.sales.services import confirm_sale_invoice

        n_invoices = 40
        payment_cycle = ['cash', 'cash', 'bank', 'credit', 'cash']
        confirmed = 0
        for i in range(n_invoices):
            inv_date = self._spread_day(i, n_invoices)
            pm = payment_cycle[i % len(payment_cycle)]
            # مبيعات نقدية عابرة بلا عميل أحياناً، وآجلة دائماً بعميل
            if pm == 'credit':
                customer = customers[i % len(customers)]
            else:
                customer = customers[i % len(customers)] if i % 3 == 0 else None

            line_items = random.sample(items, k=min(random.choice([1, 2, 2, 3]), len(items)))

            invoice = SaleInvoice(
                tenant=tenant, customer=customer, stock=stock,
                invoice_date=inv_date, status='draft', payment_method=pm,
                delivery_type='immediate', created_by=user,
            )
            if pm == 'bank':
                invoice.bank_reference = f'BTR-{inv_date.strftime("%y%m%d")}-{i+1:02d}'
            invoice.save()

            subtotal = Decimal('0')
            for item in line_items:
                price = item.selling_price or Decimal('10')
                cost = item.cost_price or Decimal('0')
                # كمية بيع تستهدف قيمة سطر بين 5,000 و40,000 — حجم معاملة صيدلية واقعي
                qty = self._qty_for_value(price, 5000, 40000, max_qty=10)
                line_total = price * qty
                subtotal += line_total
                SaleInvoiceLine.objects.create(
                    tenant=tenant, invoice=invoice, item=item, quantity=qty,
                    unit_price=price, cost_price_snapshot=cost,
                    discount_amount=Decimal('0'), line_total=line_total,
                    created_by=user,
                )
            invoice.subtotal = subtotal
            invoice.grand_total = subtotal
            invoice.save(update_fields=['subtotal', 'grand_total'])
            try:
                confirm_sale_invoice(invoice, user)
                confirmed += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'    ⚠ بيع ({inv_date}): {e}'))

        self.stdout.write(f'  ✓ فواتير بيع: {confirmed}/{n_invoices}')

    # ── Expenses ───────────────────────────────────────────────────────────
    def _create_expenses(self, tenant, user, cash_treasury):
        from apps.expenses.models import ExpenseCategory, Expense
        from apps.expenses.services import confirm_expense

        cats = {}
        for name in EXPENSE_CATEGORIES:
            cat, _ = ExpenseCategory.objects.get_or_create(tenant=tenant, name=name, defaults=dict(created_by=user))
            cats[name] = cat

        # المبالغ الكبيرة تُدفع بنكياً (لا تمسّ رصيد الخزينة النقدية)، والصغيرة نقداً
        data = [
            ('إيجار المحل', 'إيجار', 4500000, 'bank'),
            ('فاتورة كهرباء', 'كهرباء وماء', 380000, 'bank'),
            ('فاتورة مياه', 'كهرباء وماء', 90000, 'cash'),
            ('بنزين ومواصلات التوصيل', 'مواصلات ونقل', 55000, 'cash'),
            ('صيانة جهاز التبريد', 'صيانة', 650000, 'bank'),
            ('مستلزمات مكتبية وطباعة', 'متنوع', 45000, 'cash'),
            ('مكافأة أداء الفريق', 'رواتب ومكافآت', 300000, 'bank'),
            ('نظافة وتعقيم المحل', 'صيانة', 35000, 'cash'),
            ('اشتراك إنترنت الفرع', 'متنوع', 120000, 'bank'),
            ('مواصلات توريد بضاعة', 'مواصلات ونقل', 40000, 'cash'),
        ]
        created = 0
        for i, (desc, cat_name, amount, pm) in enumerate(data):
            exp_date = self._spread_day(i, len(data))
            exp = Expense.objects.create(
                tenant=tenant, category=cats[cat_name], description=desc,
                amount=Decimal(str(amount)), expense_date=exp_date,
                payment_method=pm, treasury=cash_treasury,
                status='draft', created_by=user,
            )
            try:
                confirm_expense(exp, user)
                created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'    ⚠ مصروف: {e}'))

        self.stdout.write(f'  ✓ مصروفات: {created}/{len(data)}')

    # ── Employees ──────────────────────────────────────────────────────────
    def _create_employees(self, tenant, user, cash_treasury):
        from apps.employees.models import Employee, EmployeeAdvance, EmployeeSalaryPayment

        existing = {e.name for e in Employee.objects.filter(tenant=tenant)}
        created = 0
        for name, pos, dept, stype, salary in EMPLOYEES:
            if name in existing:
                continue
            emp = Employee.objects.create(
                tenant=tenant, name=name, position=pos, department=dept,
                salary_type=stype, base_salary=Decimal(str(salary)),
                hire_date=self.today - timedelta(days=random.randint(60, 400)),
                is_active=True, created_by=user,
            )
            EmployeeAdvance.objects.create(
                tenant=tenant, employee=emp,
                amount=Decimal(str(random.randint(20000, 60000))),
                date=self.today - timedelta(days=random.randint(1, self.days)),
                payment_method='cash', treasury=cash_treasury,
                status='pending', created_by=user,
            )
            prev_month = max(1, self.today.month - 1)
            EmployeeSalaryPayment.objects.create(
                tenant=tenant, employee=emp,
                period_start=date(self.today.year, prev_month, 1),
                period_end=date(self.today.year, prev_month, 28),
                base_salary=emp.base_salary, bonus=Decimal('0'),
                advances_deducted=Decimal('0'), deductions=Decimal('0'),
                payment_method='cash', treasury=cash_treasury,
                status='paid', created_by=user,
            )
            created += 1
        self.stdout.write(f'  ✓ موظفون جدد: {created}')

    # ── Store orders ───────────────────────────────────────────────────────
    def _create_store_orders(self, tenant, items):
        from apps.store.models import StoreSettings, OnlineOrder, OnlineOrderLine
        store = StoreSettings.objects.filter(tenant=tenant).first()
        if not store or not items:
            return
        statuses = ['pending', 'approved', 'pending', 'rejected', 'approved']
        created = 0
        for i, status in enumerate(statuses):
            item = items[i % len(items)]
            qty = Decimal(str(random.randint(1, 3)))
            order = OnlineOrder.objects.create(
                tenant=tenant, store=store,
                customer_name=f'زبون أونلاين {i+1}',
                customer_phone=f'091{i}22334{i}',
                payment_method='bank', status=status,
                subtotal=item.selling_price * qty,
                total_amount=item.selling_price * qty,
            )
            OnlineOrderLine.objects.create(
                tenant=tenant, order=order, item=item,
                item_name=item.name, unit_price=item.selling_price, quantity=qty,
            )
            # created_at is auto_now_add — backdate via a direct update
            backdated_date = self.today - timedelta(days=random.randint(0, self.days - 1))
            backdated = timezone.make_aware(datetime.combine(backdated_date, dtime(hour=random.randint(9, 20))))
            OnlineOrder.objects.filter(pk=order.pk).update(created_at=backdated)
            created += 1
        self.stdout.write(f'  ✓ طلبات متجر إلكتروني: {created}')

    # ── Notifications ──────────────────────────────────────────────────────
    def _create_notifications(self, tenant, user, items, customers):
        from apps.notifications.models import Notification
        low_item = min(items, key=lambda i: i.selling_price).name if items else 'صنف'
        credit_customer = next((c for c in customers if (c.credit_limit or 0) > 0), None)
        data = [
            ('low_stock', 'high', 'تنبيه مخزون منخفض', f'الصنف «{low_item}» يقترب من الحد الأدنى للمخزون'),
            ('online_order', 'high', 'طلب جديد من المتجر', 'لديك طلبات جديدة بانتظار المراجعة في المتجر الإلكتروني'),
            ('general', 'low', 'ملخص أسبوعي', 'تم تسجيل حركة نشطة هذا الأسبوع عبر المبيعات والمشتريات'),
        ]
        if credit_customer:
            data.append((
                'overdue_invoice', 'medium', 'متابعة رصيد عميل',
                f'العميل «{credit_customer.name}» لديه رصيد آجل يستحق المتابعة',
            ))
        created = 0
        for ntype, priority, title, msg in data:
            Notification.objects.create(
                tenant=tenant, user=user, notification_type=ntype,
                priority=priority, title=title, message=msg, is_read=False,
            )
            created += 1
        self.stdout.write(f'  ✓ إشعارات: {created}')
