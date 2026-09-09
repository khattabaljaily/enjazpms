"""
seed_demo — ينشئ مشتركاً تجريبياً غنياً بالبيانات لأغراض العرض والإعلان.

الاستخدام:
    python manage.py seed_demo
    python manage.py seed_demo --delete
"""
import math
import os
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

User = get_user_model()

DEMO_SLUG     = 'enjaz-demo-pharmacy'
DEMO_PASSWORD = 'Demo@1234'
MEDIA_ITEMS   = None   # resolved at runtime


# ── بيانات الأصناف ─────────────────────────────────────────────────────────
# (name, category_idx, cost, sell, purchase_qty, emoji_label, color_hex)
ITEMS_DATA = [
    # أدوية (cat 0) — أزرق
    ('باراسيتامول 500 مجم',         0,  2800, 3500,  30, 'PARA',    '#1565C0'),
    ('أموكسيسيلين 500 مجم كبسول',   0,  3200, 4200,  25, 'AMOX',    '#0D47A1'),
    ('إيبوبروفين 400 مجم',           0,  5500, 6800,  20, 'IBU',     '#1976D2'),
    ('شراب خافض حرارة للأطفال',      0,   450,  650,  60, 'FEVR',    '#1E88E5'),
    ('أقراص ضغط الدم (أملوديبين)',   0,   980, 1300,  15, 'BP',      '#2196F3'),
    # مستلزمات طبية (cat 1) — وردي
    ('ضمادات معقّمة',                1,    55,   95, 120, 'BAND',    '#AD1457'),
    ('حقن إنسولين',                  1,    80,  145, 100, 'INSU',    '#C2185B'),
    ('كمامات طبية (علبة 50)',        1,    65,  120,  80, 'MASK',    '#D81B60'),
    ('قفازات طبية (علبة 100)',       1,   120,  220,  70, 'GLOV',    '#E91E63'),
    ('ترمومتر رقمي',                 1,   180,  320,  50, 'THRM',    '#EC407A'),
    # العناية بالبشرة (cat 2) — أخضر
    ('كريم مرطب للبشرة الجافة',      2,  1.80, 2.80, 500, 'MOIS',    '#2E7D32'),
    ('غسول وجه لطيف',                2,  4.20, 6.50, 300, 'FACE',    '#388E3C'),
    ('واقي شمسي SPF50',              2,  1.50, 2.20, 600, 'SPF',     '#43A047'),
    ('كريم علاج حب الشباب',          2, 18.0, 28.0, 200, 'ACNE',    '#4CAF50'),
    ('صابون مطهر لليدين',            2, 12.0, 20.0, 250, 'SOAP',    '#66BB6A'),
    # الأمومة والطفولة (cat 3) — بنفسجي
    ('حفاضات أطفال مقاس متوسط',      3,    95,  180,  80, 'DIAP',    '#7B1FA2'),
    ('حليب أطفال صناعي',             3,   350,  550,  40, 'MILK',    '#8E24AA'),
    ('فيتامينات ما قبل الولادة',      3,   250,  420,  60, 'PRE',     '#9C27B0'),
    ('محلول ملحي للاستنشاق',         3,    45,   85,  90, 'SALN',    '#AB47BC'),
    ('معقم يدين 500 مل',             3,    60,  110, 100, 'SANI',    '#BA68C8'),
]

# ── فواتير البيع: (days_ago, customer_idx, payment, [(item_idx, qty)]) ──────
# كلها من stock1 — كميات محسوبة لتبقى الأرصدة موجبة
SALES_DATA = [
    (58, 0, 'cash',   [(0,2),(1,1)]),
    (55, 1, 'credit', [(2,1),(3,3)]),
    (52, 2, 'cash',   [(5,5),(6,3)]),
    (50, 3, 'cash',   [(10,20),(11,8)]),
    (47, 4, 'bank',   [(15,3),(16,2)]),
    (45, 0, 'credit', [(3,2),(4,1)]),
    (43, None,'cash', [(7,4),(8,2)]),
    (40, 1, 'cash',   [(12,15),(13,6)]),
    (38, 5, 'bank',   [(17,4),(18,5)]),
    (35, 2, 'cash',   [(0,1),(1,1),(2,1)]),
    (30, 3, 'credit', [(5,3),(6,4)]),
    (28, 4, 'cash',   [(10,30),(14,10)]),
    (25, None,'cash', [(19,8),(15,2)]),
    (22, 0, 'bank',   [(2,1),(3,2)]),
    (18, 1, 'cash',   [(6,3),(7,3)]),
    (15, 5, 'credit', [(16,2),(17,2)]),
    (10, 2, 'cash',   [(11,5),(12,20)]),
    ( 7, 3, 'cash',   [(1,1),(2,1)]),
    ( 3, 4, 'bank',   [(18,2),(19,3)]),
    ( 1, None,'cash', [(4,1),(5,3)]),
]


class Command(BaseCommand):
    help = 'ينشئ بيانات demo غنية لأغراض العرض'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', help='احذف المشترك التجريبي أولاً')

    def handle(self, *args, **options):
        global MEDIA_ITEMS
        from django.conf import settings
        MEDIA_ITEMS = os.path.join(settings.MEDIA_ROOT, 'items')
        os.makedirs(MEDIA_ITEMS, exist_ok=True)

        from apps.core.models import Tenant
        if options['delete']:
            self._delete_demo_tenant()
            self.stdout.write('✓ تم حذف المشترك التجريبي القديم')

        if Tenant.objects.filter(slug=DEMO_SLUG).exists():
            self.stdout.write(self.style.WARNING('موجود — استخدم --delete'))
            return

        self.stdout.write('⏳ جاري إنشاء البيانات التجريبية...')

        with transaction.atomic():
            tenant     = self._create_tenant()
            user       = self._create_user(tenant)
            cash_t, _  = self._create_treasuries(tenant, user)
            stock1, _  = self._create_stocks(tenant, user)
            categories = self._create_categories(tenant, user)
            units      = self._create_units(tenant, user)
            items      = self._create_items(tenant, user, categories, units)
            customers  = self._create_customers(tenant, user)
            suppliers  = self._create_suppliers(tenant, user)
            self._create_purchases(tenant, user, stock1, suppliers, items)
            self._create_sales(tenant, user, stock1, customers, items)
            self._create_expenses(tenant, user, cash_t)
            self._create_employees(tenant, user, cash_t)
            self._create_store(tenant, items)
            self._create_notifications(tenant, user)

        self.stdout.write(self.style.SUCCESS('✅ اكتملت البيانات التجريبية!'))
        self._print_credentials(tenant)

    # ── حذف ────────────────────────────────────────────────────────────────
    def _delete_demo_tenant(self):
        from apps.core.models import Tenant
        from django.db import connection
        t = Tenant.objects.filter(slug=DEMO_SLUG).first()
        if not t:
            return
        tid = t.id
        tables = [
            'expenses','expense_categories',
            'supplier_ledger','purchase_payments',
            'purchase_return_lines','purchase_returns',
            'purchase_rfq_lines','purchase_rfq',
            'purchase_invoice_lines','purchase_invoices',
            'store_online_order_lines','store_online_orders','store_settings',
            'employee_advances','employee_incentives',
            'employee_salary_payments','employees',
            'treasury_movements','treasuries',
            'sale_payments','customer_ledger',
            'sale_return_lines','sale_returns',
            'sale_quote_lines','sale_quotes',
            'sale_invoice_lines','sale_invoices',
            'stock_movements','stock_transfer_lines','stock_transfers',
            'stocktake_lines','stocktakes','manufacturing_orders',
            'stock_quantities','stocks',
            'notifications','activity_logs','user_activities',
            'item_batches','bom_lines','bom_recipes',
            'item_units','items','item_categories',
            'customers','suppliers',
            'users','tenant_capabilities','tenant_settings',
            'tenant_backups','support_messages','support_tickets',
            'tenants',
        ]
        with connection.cursor() as cursor:
            cursor.execute('SET FOREIGN_KEY_CHECKS = 0')
            for tbl in tables:
                try:
                    cursor.execute(f'DELETE FROM `{tbl}` WHERE tenant_id = %s', [tid])
                except Exception:
                    try:
                        cursor.execute(f'DELETE FROM `{tbl}` WHERE id = %s', [tid])
                    except Exception:
                        pass
            cursor.execute('SET FOREIGN_KEY_CHECKS = 1')

    # ── Tenant ─────────────────────────────────────────────────────────────
    def _create_tenant(self):
        from apps.core.models import Tenant, BusinessType, TenantCapabilities, Settings
        bt = BusinessType.objects.first()
        if not bt:
            bt = BusinessType.objects.create(
                name='pharmacy', name_ar='صيدلية',
                slug='pharmacy', icon='fa-pills',
            )
        today = date.today()
        tenant = Tenant.objects.create(
            name='صيدلية الشفاء', slug=DEMO_SLUG,
            business_type=bt,
            email='info@alshifa.demo', phone='0912345678',
            address='شارع الجامعة، المنطقة التجارية',
            city='الخرطوم', country='السودان',
            subscription_plan='pro',
            subscription_start=today,
            subscription_expires=today + timedelta(days=365),
            is_active=True, is_demo=True,
            version_type='multi_stock',
            max_branches=1, max_stocks=5, max_users=20,
            timezone='Africa/Khartoum', language='ar', currency='SDG',
        )
        TenantCapabilities.objects.update_or_create(
            tenant=tenant,
            defaults=dict(has_services=True, has_expiry_dates=True, has_batch_numbers=True),
        )
        Settings.objects.update_or_create(
            tenant=tenant,
            defaults=dict(
                invoice_prefix='SHF',
                invoice_footer='شكراً لتعاملكم معنا — صيدلية الشفاء',
                invoice_color='#f6a821',
                low_stock_alert=True,
            ),
        )
        self.stdout.write(f'  ✓ مشترك: {tenant.name}')
        return tenant

    # ── User ───────────────────────────────────────────────────────────────
    def _create_user(self, tenant):
        user = User.objects.create_user(
            username='demo_admin', email='admin@alshifa.demo',
            password=DEMO_PASSWORD,
            first_name='أحمد', last_name='محمد',
            tenant=tenant, is_tenant_admin=True, is_active=True,
        )
        self.stdout.write('  ✓ مستخدم: demo_admin')
        return user

    # ── Treasuries ─────────────────────────────────────────────────────────
    def _create_treasuries(self, tenant, user):
        from apps.treasury.models import Treasury
        from apps.treasury.services import post_treasury_receipt
        cash = Treasury.objects.create(
            tenant=tenant, name='الخزينة الرئيسية', code='CASH-01',
            is_default=True, is_system_default=True, current_balance=Decimal('0'),
            created_by=user,
        )
        bank = Treasury.objects.create(
            tenant=tenant, name='البنك الأهلي', code='BANK-01',
            is_default=False, current_balance=Decimal('0'),
            created_by=user,
        )
        # رصيد افتتاحي نقدي 200,000
        post_treasury_receipt(
            tenant=tenant, amount=Decimal('200000'),
            date=date.today() - timedelta(days=90),
            reference_type='opening_balance',
            description='رصيد افتتاحي',
            treasury=cash,
        )
        self.stdout.write('  ✓ خزائن: 2  (رصيد افتتاحي 200,000)')
        return cash, bank

    # ── Stocks ─────────────────────────────────────────────────────────────
    def _create_stocks(self, tenant, user):
        from apps.stocks.models import Stock
        from django.db import connection
        # MySQL may reuse tenant IDs after --delete; purge any stale stock data
        with connection.cursor() as cursor:
            cursor.execute('SET FOREIGN_KEY_CHECKS = 0')
            cursor.execute('DELETE FROM stock_quantities WHERE tenant_id = %s', [tenant.id])
            cursor.execute('DELETE FROM stock_movements WHERE tenant_id = %s', [tenant.id])
            cursor.execute('DELETE FROM stocks WHERE tenant_id = %s', [tenant.id])
            cursor.execute('SET FOREIGN_KEY_CHECKS = 1')
        s1 = Stock.objects.create(
            tenant=tenant, name='المخزن الرئيسي', code='STK-01',
            stock_type='main', is_default=True, is_active=True,
            created_by=user,
        )
        s2 = Stock.objects.create(
            tenant=tenant, name='مخزن الطابق الثاني', code='STK-02',
            stock_type='main', is_default=False, is_active=True,
            created_by=user,
        )
        self.stdout.write('  ✓ مخازن: 2')
        return s1, s2

    # ── Categories ─────────────────────────────────────────────────────────
    def _create_categories(self, tenant, user):
        from apps.items.models import Category
        names = ['أدوية', 'مستلزمات طبية', 'العناية بالبشرة', 'الأمومة والطفولة']
        cats = [
            Category.objects.create(tenant=tenant, name=n, is_active=True, created_by=user)
            for n in names
        ]
        self.stdout.write(f'  ✓ تصنيفات: {len(cats)}')
        return cats

    # ── Units ──────────────────────────────────────────────────────────────
    def _create_units(self, tenant, user):
        from apps.items.models import Unit
        data = [('قطعة','PCS'),('علبة','BOX'),('شريط','STRIP'),('مل','ML')]
        units = [
            Unit.objects.create(
                tenant=tenant, name=n, abbreviation=a,
                conversion_factor=Decimal('1'), is_active=True, created_by=user,
            )
            for n, a in data
        ]
        self.stdout.write(f'  ✓ وحدات: {len(units)}')
        return units

    # ── Items + صور ────────────────────────────────────────────────────────
    def _create_items(self, tenant, user, categories, units):
        from apps.items.models import Item
        items = []
        for idx, (name, cat_idx, cost, sell, _, label, color) in enumerate(ITEMS_DATA):
            unit = units[0]   # قطعة لكل الأصناف
            img_path = self._make_product_image(label, color, idx)
            item = Item.objects.create(
                tenant=tenant,
                name=name,
                category=categories[cat_idx],
                unit=unit,
                item_type='product',
                cost_price=Decimal(str(cost)),
                selling_price=Decimal(str(sell)),
                sku=f'SKU-{idx+1:03d}',
                image=img_path,
                is_active=True,
                is_sellable=True,
                is_purchasable=True,
                created_by=user,
            )
            items.append(item)
        self.stdout.write(f'  ✓ أصناف: {len(items)} (مع صور)')
        return items

    def _make_product_image(self, label, hex_color, idx):
        """توليد صورة منتج ملونة باستخدام Pillow"""
        try:
            from PIL import Image, ImageDraw
            size = 400

            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)

            img = Image.new('RGB', (size, size), (r, g, b))
            draw = ImageDraw.Draw(img)

            # gradient أفتح في المنتصف
            for y in range(size):
                alpha = 1 - (abs(y - size/2) / (size/2)) * 0.4
                light_r = min(255, int(r + (255-r) * (1-alpha) * 0.5))
                light_g = min(255, int(g + (255-g) * (1-alpha) * 0.5))
                light_b = min(255, int(b + (255-b) * (1-alpha) * 0.5))
                draw.line([(0, y), (size, y)], fill=(light_r, light_g, light_b))

            # دائرة بيضاء شفافة في المنتصف
            cx, cy, r_circle = size//2, size//2, size//3
            draw.ellipse(
                [cx-r_circle, cy-r_circle, cx+r_circle, cy+r_circle],
                fill=(255, 255, 255, 0),
                outline=(255, 255, 255),
                width=6,
            )

            # نقاط زخرفية
            for i in range(8):
                angle = i * math.pi / 4
                px = int(cx + r_circle * 0.7 * math.cos(angle))
                py = int(cy + r_circle * 0.7 * math.sin(angle))
                draw.ellipse([px-8, py-8, px+8, py+8], fill=(255,255,255))

            # شعار المنتج (أحرف إنجليزية — PIL لا يدعم العربية بدون مكتبات إضافية)
            try:
                from PIL import ImageFont
                font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
                font_big  = ImageFont.truetype(font_path, 48)
                font_small = ImageFont.truetype(font_path, 22)
            except Exception:
                font_big = font_small = None

            # label
            draw.text((cx, cy - 20), label, fill='white', font=font_big, anchor='mm')
            draw.text((cx, cy + 40), f'ITEM {idx+1:02d}', fill=(255,255,255,180),
                      font=font_small, anchor='mm')

            filename = f'demo_item_{idx+1:02d}.png'
            filepath = os.path.join(MEDIA_ITEMS, filename)
            img.save(filepath, 'PNG', optimize=True)
            return f'items/{filename}'
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'    ⚠ صورة {label}: {e}'))
            return ''

    # ── Customers ──────────────────────────────────────────────────────────
    def _create_customers(self, tenant, user):
        from apps.customers.models import Customer
        data = [
            ('عيادة الأمل الطبية',       '0912111222', 'الخرطوم',  20000),
            ('مركز البركة الصحي',        '0922333444', 'أمدرمان',  15000),
            ('صيدلية النور الفرعية',     '0933555666', 'بحري',      5000),
            ('عبدالرحمن إبراهيم سعيد',   '0944777888', 'الخرطوم',      0),
            ('مستشفى الخليج التخصصي',    '0955999000', 'شرق النيل', 30000),
            ('مريم عبدالله كرم',         '0966111333', 'أمدرمان',      0),
        ]
        customers = [
            Customer.objects.create(
                tenant=tenant, name=n, phone=p, city=c,
                credit_limit=Decimal(str(cl)), created_by=user,
            )
            for n, p, c, cl in data
        ]
        self.stdout.write(f'  ✓ عملاء: {len(customers)}')
        return customers

    # ── Suppliers ──────────────────────────────────────────────────────────
    def _create_suppliers(self, tenant, user):
        from apps.suppliers.models import Supplier
        data = [
            ('الشركة الوطنية لتوريد الأدوية',        '0911222333', 'الخرطوم'),
            ('مصنع الدواء السوداني',                 '0922444555', 'شندي'),
            ('مجموعة الرعاية للمستلزمات الطبية',      '0933666777', 'أمدرمان'),
            ('موردو مستحضرات التجميل الدولية',        '0944888999', 'الخرطوم'),
        ]
        suppliers = [
            Supplier.objects.create(
                tenant=tenant, name=n, phone=p, city=c, created_by=user,
            )
            for n, p, c in data
        ]
        self.stdout.write(f'  ✓ موردون: {len(suppliers)}')
        return suppliers

    # ── Purchase Invoices (16 — كلها إلى stock1 credit) ───────────────────
    def _create_purchases(self, tenant, user, stock, suppliers, items):
        from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine
        from apps.purchases.services import confirm_purchase_invoice

        today = date.today()
        # كل صنف يُشتر بكمية كافية (من ITEMS_DATA purchase_qty)
        # نقسّمها على دفعتين لبعض الأصناف لإظهار فواتير متعددة
        batches = [
            # (days_ago, supplier_idx, item_indices)
            (65, 0, [0,1,2,3,4]),          # أدوية - دفعة 1
            (55, 0, [0,1,2]),              # أدوية - دفعة 2
            (60, 1, [5,6,7,8,9]),          # مستلزمات طبية - دفعة 1
            (50, 1, [5,6,7]),              # مستلزمات طبية - دفعة 2
            (58, 2, [10,11,12,13,14]),     # العناية بالبشرة - دفعة 1
            (48, 2, [10,12,14]),           # العناية بالبشرة - دفعة 2
            (56, 3, [15,16,17,18,19]),     # الأمومة والطفولة - دفعة 1
            (45, 3, [15,16,17]),           # الأمومة والطفولة - دفعة 2
            (30, 0, [2,3,4]),              # أدوية - إعادة تخزين
            (25, 1, [8,9]),                # مستلزمات طبية - إعادة تخزين
            (15, 2, [11,13]),              # العناية بالبشرة - إعادة تخزين
            (10, 3, [18,19]),              # الأمومة والطفولة - إعادة تخزين
        ]

        qty_factors = [1.0, 0.5, 1.0, 0.5, 1.0, 0.5, 1.0, 0.5, 0.3, 0.3, 0.3, 0.3]

        confirmed = 0
        for (days_ago, sup_idx, item_indices), qf in zip(batches, qty_factors):
            invoice = PurchaseInvoice(
                tenant=tenant, supplier=suppliers[sup_idx], stock=stock,
                invoice_date=today - timedelta(days=days_ago),
                status='draft', payment_method='credit', created_by=user,
            )
            invoice.save()
            subtotal = Decimal('0')
            for i in item_indices:
                name, cat_idx, cost, sell, base_qty, *_ = ITEMS_DATA[i]
                qty = max(1, int(base_qty * qf))
                line_total = Decimal(str(cost)) * qty
                subtotal += line_total
                PurchaseInvoiceLine.objects.create(
                    tenant=tenant, invoice=invoice,
                    item=items[i], quantity=Decimal(str(qty)),
                    unit_cost=Decimal(str(cost)),
                    line_total=line_total, created_by=user,
                )
            invoice.subtotal = subtotal
            invoice.grand_total = subtotal
            invoice.save(update_fields=['subtotal','grand_total'])
            try:
                confirm_purchase_invoice(invoice, user)
                confirmed += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'    ⚠ شراء: {e}'))

        self.stdout.write(f'  ✓ فواتير شراء: {confirmed}/{len(batches)}')

    # ── Sale Invoices (20 — كلها من stock1) ───────────────────────────────
    def _create_sales(self, tenant, user, stock, customers, items):
        from apps.sales.models import SaleInvoice, SaleInvoiceLine
        from apps.sales.services import confirm_sale_invoice

        today = date.today()
        confirmed = 0
        for days_ago, cust_idx, payment, lines in SALES_DATA:
            customer = customers[cust_idx] if cust_idx is not None else None
            invoice = SaleInvoice(
                tenant=tenant, customer=customer, stock=stock,
                invoice_date=today - timedelta(days=days_ago),
                status='draft', payment_method=payment,
                delivery_type='immediate', created_by=user,
            )
            invoice.save()
            subtotal = Decimal('0')
            for item_idx, qty in lines:
                price = Decimal(str(ITEMS_DATA[item_idx][3]))   # sell price
                cost  = Decimal(str(ITEMS_DATA[item_idx][2]))   # cost
                line_total = price * qty
                subtotal += line_total
                SaleInvoiceLine.objects.create(
                    tenant=tenant, invoice=invoice,
                    item=items[item_idx],
                    quantity=Decimal(str(qty)),
                    unit_price=price,
                    cost_price_snapshot=cost,
                    discount_amount=Decimal('0'),
                    line_total=line_total,
                    created_by=user,
                )
            invoice.subtotal = subtotal
            invoice.grand_total = subtotal
            invoice.save(update_fields=['subtotal','grand_total'])
            try:
                confirm_sale_invoice(invoice, user)
                confirmed += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'    ⚠ بيع ({days_ago}d): {e}'))

        self.stdout.write(f'  ✓ فواتير بيع: {confirmed}/{len(SALES_DATA)}')

    # ── Expenses (محدودة لضمان ربح موجب) ─────────────────────────────────
    def _create_expenses(self, tenant, user, cash_treasury):
        from apps.expenses.models import ExpenseCategory, Expense
        from apps.expenses.services import confirm_expense

        cats = {}
        for name in ['إيجار','كهرباء وماء','مواصلات','صيانة','متنوع']:
            cats[name] = ExpenseCategory.objects.create(
                tenant=tenant, name=name, created_by=user,
            )

        today = date.today()
        data = [
            (cats['إيجار'],      'إيجار المحل — مايو',    4500, today-timedelta(days=55)),
            (cats['كهرباء وماء'],'فاتورة كهرباء',          480, today-timedelta(days=45)),
            (cats['كهرباء وماء'],'فاتورة مياه',            150, today-timedelta(days=45)),
            (cats['مواصلات'],    'بنزين ومواصلات',         380, today-timedelta(days=30)),
            (cats['إيجار'],      'إيجار المحل — يونيو',   4500, today-timedelta(days=25)),
            (cats['صيانة'],      'صيانة التكييف',          800, today-timedelta(days=15)),
            (cats['متنوع'],      'مستلزمات مكتبية',        250, today-timedelta(days=8)),
        ]
        for cat, desc, amount, exp_date in data:
            exp = Expense.objects.create(
                tenant=tenant, category=cat, description=desc,
                amount=Decimal(str(amount)), expense_date=exp_date,
                payment_method='cash', treasury=cash_treasury,
                status='draft', created_by=user,
            )
            try:
                confirm_expense(exp, user)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'    ⚠ مصروف: {e}'))

        total = sum(d[2] for d in data)
        self.stdout.write(f'  ✓ مصروفات: {len(data)} (إجمالي {total:,} SDG)')

    # ── Employees ──────────────────────────────────────────────────────────
    def _create_employees(self, tenant, user, cash_treasury):
        from apps.employees.models import Employee, EmployeeAdvance, EmployeeSalaryPayment

        today = date.today()
        emp_data = [
            ('محمد أحمد علي',  'صيدلي مسؤول',   'الصيدلة',   'fixed', 3500),
            ('فاطمة إبراهيم',  'محاسبة',        'المحاسبة',  'fixed', 2800),
            ('خالد عبدالله',   'مسؤول المخزن',  'المخزن',    'fixed', 2200),
        ]
        for name, pos, dept, stype, salary in emp_data:
            emp = Employee.objects.create(
                tenant=tenant, name=name, position=pos, department=dept,
                salary_type=stype, base_salary=Decimal(str(salary)),
                hire_date=today-timedelta(days=random.randint(90,365)),
                is_active=True, created_by=user,
            )
            EmployeeAdvance.objects.create(
                tenant=tenant, employee=emp,
                amount=Decimal(str(random.randint(200,400))),
                date=today-timedelta(days=12),
                payment_method='cash', treasury=cash_treasury,
                status='pending', created_by=user,
            )
            period_start = date(today.year, max(1, today.month-1), 1)
            period_end   = date(today.year, max(1, today.month-1), 28)
            EmployeeSalaryPayment.objects.create(
                tenant=tenant, employee=emp,
                period_start=period_start, period_end=period_end,
                base_salary=emp.base_salary, bonus=Decimal('200'),
                advances_deducted=Decimal('0'), deductions=Decimal('0'),
                payment_method='cash', treasury=cash_treasury,
                status='paid', created_by=user,
            )
        self.stdout.write(f'  ✓ موظفون: {len(emp_data)}')

    # ── Store ──────────────────────────────────────────────────────────────
    def _create_store(self, tenant, items):
        from apps.store.models import StoreSettings, OnlineOrder, OnlineOrderLine
        StoreSettings.objects.filter(tenant=tenant).delete()
        store = StoreSettings.objects.create(
            tenant=tenant, is_enabled=True,
            display_name='متجر صيدلية الشفاء', description='أدوية ومستلزمات طبية أصلية بأفضل الأسعار',
            accent_color='#f6a821', show_prices=True,
            show_stock_quantity=False, status_override='open',
        )
        for i, status in enumerate(['pending','approved','pending']):
            item = items[i * 5]
            order = OnlineOrder.objects.create(
                tenant=tenant, store=store,
                customer_name=f'عميل أونلاين {i+1}',
                customer_phone=f'091{i}000111',
                payment_method='bank', status=status,
                subtotal=item.selling_price * 2,
                total_amount=item.selling_price * 2,
            )
            OnlineOrderLine.objects.create(
                tenant=tenant, order=order, item=item,
                item_name=item.name, unit_price=item.selling_price,
                quantity=Decimal('2'),
            )
        self.stdout.write('  ✓ متجر إلكتروني + 3 طلبات')

    # ── Notifications ──────────────────────────────────────────────────────
    def _create_notifications(self, tenant, user):
        from apps.notifications.models import Notification
        data = [
            ('low_stock',      'high',   'تنبيه مخزون منخفض',           'الصنف «شراب خافض حرارة للأطفال» وصل للحد الأدنى (5 قطع)'),
            ('online_order',   'high',   'طلب جديد من المتجر',           'طلب #ORD-00003 بقيمة 7,000 SDG — في انتظار الموافقة'),
            ('overdue_invoice','medium', 'فاتورة متأخرة السداد',          'فاتورة «مركز البركة الصحي» متأخرة 12 يوماً'),
            ('transfer_done',  'low',    'اكتمل تحويل المخزون',           'تم تأكيد التحويل TRF-001 إلى مخزن الطابق الثاني'),
            ('general',        'low',    'مرحباً في ENJAZ PMS',               'حسابك جاهز — ابدأ بإضافة أدويتك ومبيعاتك'),
        ]
        for ntype, priority, title, msg in data:
            Notification.objects.create(
                tenant=tenant, user=user, notification_type=ntype,
                priority=priority, title=title, message=msg, is_read=False,
            )
        self.stdout.write(f'  ✓ إشعارات: {len(data)}')

    # ── بيانات الدخول ─────────────────────────────────────────────────────
    def _print_credentials(self, tenant):
        self.stdout.write('\n' + '═'*50)
        self.stdout.write('  بيانات الدخول')
        self.stdout.write('═'*50)
        self.stdout.write(f'  المشترك    : {tenant.name}')
        self.stdout.write(f'  اسم الدخول : demo_admin')
        self.stdout.write(f'  كلمة المرور: {DEMO_PASSWORD}')
        self.stdout.write('═'*50)
