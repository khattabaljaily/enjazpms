# -*- coding: utf-8 -*-
"""
seed_valoria_week — يملأ مشترك «شركة فالوريا» (موزّع أدوية) ببيانات تجريبية
واقعية عبر كل أقسام النظام تقريباً (مبيعات، مشتريات، مصروفات، خزينة،
حسابات بنكية، مناديب، تأمين، مخازن، متجر إلكتروني، دعم فني...)، بتواريخ
موزّعة على آخر N يوم (افتراضياً 7 — من اليوم رجوعاً)، مع ضمان أن إجمالي
المبيعات أعلى من إجمالي المصروفات بفارق كبير.

الاستخدام:
    python manage.py seed_valoria_week
    python manage.py seed_valoria_week --tenant شركة-فالوريا --days 7
    python manage.py seed_valoria_week --tenant شركة-فالوريا --months 6 --profit-boost 1.25

مع --months (أو --days كبير) تتضاعف أحجام كل الحركات تناسبياً مع طول الفترة
(scale = days/7)، وتُولَّد المصروفات والرواتب وأسعار الصرف بشكل شهري دوري،
وتُضبط الأرصدة الافتتاحية وحدود الائتمان بما يناسب الحجم.
--dry-run ينفّذ كل شيء داخل معاملة ثم يتراجع عنها (للاختبار).
"""
import random
from datetime import date, datetime, timedelta, time as dtime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import transaction

User = get_user_model()


# ── تصنيفات ──────────────────────────────────────────────────────────────
CATEGORIES = [
    'أدوية', 'مستلزمات طبية', 'أجهزة طبية', 'مستحضرات تجميل وعناية',
    'مكملات غذائية', 'منتجات أطفال ورضاعة', 'مطهرات ومعقمات',
    'مستلزمات مستشفيات وعيادات', 'أدوات مخبرية وتشخيصية', 'منتجات موسمية',
]

# ── وحدات القياس (name, abbreviation) ──────────────────────────────────────
UNITS = [
    ('قطعة', 'قطعة'), ('علبة', 'علبة'), ('كرتون', 'كرتون'), ('شريط', 'شريط'),
    ('زجاجة', 'زجاجة'), ('كيس', 'كيس'), ('طقم', 'طقم'), ('أنبوب', 'أنبوب'),
    ('كرتونة كبيرة', 'ك.كبيرة'), ('برميل', 'برميل'),
]

# (name, category_idx, cost, sell, unit_idx, track_batch, prefix,
#  generic_name, manufacturer, country, dosage_form)
ITEMS_DATA = [
    ('باراسيتامول 500 مجم — كرتون', 0, 180000, 245000, 2, True, 'PARA',
     'Paracetamol', 'سبكو', 'السودان', 'أقراص'),
    ('أموكسيسيلين 500 مجم كبسول — كرتون', 0, 320000, 430000, 2, True, 'AMOX',
     'Amoxicillin', 'GSK', 'مصر', 'كبسولات'),
    ('إيبوبروفين 400 مجم — كرتون', 0, 210000, 290000, 2, True, 'IBU',
     'Ibuprofen', 'المجموعة العربية للأدوية', 'مصر', 'أقراص'),
    ('أوجمنتين 1 جم أقراص — كرتون', 0, 450000, 610000, 2, True, 'AUG',
     'Amoxicillin/Clavulanate', 'GSK', 'بريطانيا', 'أقراص'),
    ('سيتال شراب أطفال — كرتون', 0, 260000, 350000, 2, True, 'CETL',
     'Paracetamol', 'سبكو', 'السودان', 'شراب'),
    ('فيتامين سي فوّار — كرتون', 0, 190000, 260000, 2, True, 'VITC',
     'Ascorbic Acid', 'باير', 'ألمانيا', 'أقراص فوارة'),
    ('أنسولين لانتوس — علبة 5 أقلام', 0, 520000, 700000, 1, True, 'INSL',
     'Insulin Glargine', 'سانوفي', 'فرنسا', 'حقن'),
    ('أوميبرازول 20 مجم — كرتون', 0, 230000, 310000, 2, True, 'OMEP',
     'Omeprazole', 'الشركة الهندية للأدوية الجنيسة', 'الهند', 'كبسولات'),
    ('ديكلوفيناك حقن — كرتون', 0, 170000, 235000, 2, True, 'DICL',
     'Diclofenac', 'سبكو', 'السودان', 'حقن'),
    ('كلورفينامين أقراص — كرتون', 0, 140000, 190000, 2, True, 'CHLR',
     'Chlorpheniramine', 'أمدرمان للأدوية', 'السودان', 'أقراص'),
    ('قفازات طبية لاتكس — كرتون', 1, 95000, 135000, 2, False, 'GLOV', '', '', '', ''),
    ('كمامات طبية N95 — كرتون', 1, 180000, 250000, 2, False, 'MASK', '', '', '', ''),
    ('حقن إنسولين معقمة — كرتون', 1, 60000, 88000, 2, False, 'SYRI', '', '', '', ''),
    ('ضمادات وشاش طبي — كرتون', 1, 45000, 68000, 2, False, 'GAUZ', '', '', '', ''),
    ('ترمومتر رقمي — كرتون 50 قطعة', 1, 320000, 460000, 2, False, 'THRM', '', '', '', ''),
    ('جهاز قياس ضغط الدم الرقمي — كرتون', 2, 1800000, 2450000, 2, False, 'BPM', '', '', '', ''),
    ('جهاز قياس السكر التشخيصي — كرتون', 2, 2100000, 2850000, 2, False, 'GLUM', '', '', '', ''),
    ('جهاز تبخير للأطفال — كرتون', 2, 950000, 1350000, 2, False, 'NEBZ', '', '', '', ''),
    ('كريم مرطب للبشرة — كرتون', 3, 140000, 210000, 2, False, 'MOIS', '', '', '', ''),
    ('غسول وجه لطيف — كرتون', 3, 120000, 180000, 2, False, 'FACE', '', '', '', ''),
    ('أوميغا 3 كبسولات — كرتون', 4, 380000, 520000, 2, True, 'OMG3', '', '', '', ''),
    ('زنك ومعادن مناعة — كرتون', 4, 290000, 400000, 2, True, 'ZINC', '', '', '', ''),
    ('حفاضات أطفال اقتصادية — كرتونة', 5, 610000, 850000, 8, False, 'DIAP', '', '', '', ''),
    ('معقم يدين 500 مل — كرتون', 6, 210000, 300000, 2, False, 'SANI', '', '', '', ''),
]

SUPPLIERS = [
    ('الشركة السودانية للصناعات الدوائية (سبكو)', '0911100011', 'الخرطوم'),
    ('شركة أمدرمان للأدوية', '0911100022', 'أمدرمان'),
    ('المجموعة العربية للأدوية', '0911100033', 'القاهرة'),
    ('شركة النيل الأزرق للمستلزمات الطبية', '0911100044', 'الخرطوم بحري'),
    ('جلاكسو سميث كلاين — فرع السودان', '0911100055', 'الخرطوم'),
    ('شركة الخرطوم للأجهزة الطبية', '0911100066', 'الخرطوم'),
    ('مجموعة الصفوة للتوريدات الطبية', '0911100077', 'أمدرمان'),
    ('الشركة الهندية للأدوية الجنيسة', '0911100088', 'مومباي'),
    ('مؤسسة النور للمستلزمات الطبية', '0911100099', 'الخرطوم بحري'),
    ('شركة سانوفي — فرع الخليج', '0911100100', 'دبي'),
]

# (name, phone, city, credit_limit)
CUSTOMERS = [
    ('صيدلية الشفاء المركزية', '0912200011', 'الخرطوم', 8000000),
    ('صيدليات النور (سلسلة فروع)', '0912200022', 'أمدرمان', 12000000),
    ('مستشفى الرباط الوطني', '0912200033', 'الخرطوم', 20000000),
    ('عيادات السلامة التخصصية', '0912200044', 'بحري', 6000000),
    ('صيدلية الأمل — أمدرمان', '0912200055', 'أمدرمان', 5000000),
    ('مجمع الخرطوم الطبي', '0912200066', 'الخرطوم', 15000000),
    ('صيدلية دار الدواء', '0912200077', 'الخرطوم', 4000000),
    ('عيادة الأطفال التخصصية', '0912200088', 'بحري', 3000000),
    ('صيدلية بحري المركزية', '0912200099', 'بحري', 5000000),
    ('مركز النيل للرعاية الصحية', '0912200100', 'الخرطوم', 9000000),
    ('د. محمد عثمان الطيب — عيادة خاصة', '0912200111', 'الخرطوم', 0),
    ('صيدلية الحياة — مدني', '0912200122', 'مدني', 3500000),
]

AGENTS = [
    ('عصام الدين محمد أحمد', '0913300011', 'الخرطوم'),
    ('هبة الله كرم الله', '0913300022', 'أمدرمان'),
    ('مازن عبدالرحمن حسن', '0913300033', 'بحري'),
    ('رانيا الفاتح إبراهيم', '0913300044', 'الخرطوم'),
    ('وليد صديق عثمان', '0913300055', 'مدني'),
    ('نضال محمد الحسن', '0913300066', 'الخرطوم'),
    ('خالد النور آدم', '0913300077', 'أمدرمان'),
    ('سلمى إبراهيم يوسف', '0913300088', 'بحري'),
]

# (name, position, department, base_salary)
EMPLOYEES = [
    ('عمر الطيب الصادق', 'مدير مبيعات', 'المبيعات', 900000),
    ('منى حسن الأمين', 'محاسب أول', 'الحسابات', 750000),
    ('أحمد كمال الدين', 'أمين مخزن', 'المخزن', 500000),
    ('الفاتح آدم بابكر', 'سائق توزيع', 'التوزيع', 350000),
    ('حسام الدين يوسف', 'سائق توزيع', 'التوزيع', 350000),
    ('سارة محمد الحسن', 'عامل مخزن', 'المخزن', 320000),
    ('يوسف إدريس عثمان', 'عامل مخزن', 'المخزن', 320000),
    ('نوال عبدالله كرار', 'موظفة استقبال', 'الإدارة', 300000),
    ('طارق النور محجوب', 'مسؤول مشتريات', 'المشتريات', 700000),
    ('إيمان الصادق المهدي', 'مسؤولة ضبط جودة', 'الجودة', 600000),
]

BANK_ACCOUNTS = [
    ('بنك الخرطوم — حساب جاري', 'بنك الخرطوم', '1002233445', 'SDG', 0),
    ('بنك أمدرمان الوطني — حساب جاري', 'بنك أمدرمان الوطني', '2003344556', 'SDG', 0),
    ('بنك فيصل الإسلامي — حساب دولاري', 'بنك فيصل الإسلامي', '3004455667', 'USD', 0),
]

INSURANCE_COMPANIES = [
    ('شركة السلامة للتأمين الصحي', 70),
    ('شركة النيل الأزرق للتأمين', 60),
    ('الشركة الوطنية للتأمين الصحي', 80),
]

EXPENSE_CATEGORIES = [
    'إيجار', 'كهرباء وماء', 'مواصلات ونقل', 'صيانة', 'رواتب ومكافآت',
    'جمارك وشحن', 'متنوع',
]

EXPENSES_DATA = [
    # (description, category, amount, payment_method)
    ('إيجار المستودع الرئيسي', 'إيجار', 4500000, 'bank'),
    ('فاتورة كهرباء المستودع', 'كهرباء وماء', 420000, 'bank'),
    ('فاتورة مياه', 'كهرباء وماء', 95000, 'cash'),
    ('وقود شاحنات التوزيع', 'مواصلات ونقل', 380000, 'cash'),
    ('صيانة ثلاجات حفظ الأدوية', 'صيانة', 650000, 'bank'),
    ('رسوم جمركية لشحنة واردة', 'جمارك وشحن', 1200000, 'bank'),
    ('مستلزمات مكتبية وطباعة', 'متنوع', 60000, 'cash'),
    ('مكافأة أداء فريق المبيعات', 'رواتب ومكافآت', 500000, 'bank'),
    ('نظافة وتعقيم المستودع', 'صيانة', 45000, 'cash'),
    ('اشتراك إنترنت ونظام ERP', 'متنوع', 150000, 'bank'),
]

# مصروفات شهرية دورية للفترات الطويلة: (الوصف، التصنيف، المبلغ، طريقة الدفع، يوم الشهر، كل كم شهر)
MONTHLY_EXPENSES = [
    ('إيجار المستودع الرئيسي', 'إيجار', 4500000, 'bank', 1, 1),
    ('فاتورة كهرباء المستودع', 'كهرباء وماء', 420000, 'bank', 5, 1),
    ('فاتورة مياه', 'كهرباء وماء', 95000, 'cash', 6, 1),
    ('اشتراك إنترنت ونظام ERP', 'متنوع', 150000, 'bank', 10, 1),
    ('مستلزمات مكتبية وطباعة', 'متنوع', 60000, 'cash', 12, 1),
    ('صيانة ثلاجات حفظ الأدوية', 'صيانة', 650000, 'bank', 15, 2),
    ('رسوم جمركية لشحنة واردة', 'جمارك وشحن', 1200000, 'bank', 18, 1),
    ('نظافة وتعقيم المستودع', 'صيانة', 45000, 'cash', 20, 1),
    ('مكافأة أداء فريق المبيعات', 'رواتب ومكافآت', 500000, 'bank', 27, 1),
]
# مصروفات أسبوعية: (الوصف، التصنيف، المبلغ، طريقة الدفع)
WEEKLY_EXPENSES = [
    ('وقود شاحنات التوزيع', 'مواصلات ونقل', 380000, 'cash'),
]


class Command(BaseCommand):
    help = 'يملأ مشترك «شركة فالوريا» ببيانات تجريبية شاملة لكل أقسام النظام خلال آخر أسبوع'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default=None, help='slug المشترك (اختياري)')
        parser.add_argument('--days', type=int, default=7, help='عدد الأيام الماضية لتوزيع الحركات عليها')
        parser.add_argument('--months', type=int, default=None, help='بديل عن --days: عدد الأشهر الماضية (30.4 يوم/شهر)')
        parser.add_argument('--profit-boost', type=float, default=1.0,
                            help='معامل لرفع أسعار البيع للأصناف الجديدة (مثال 1.25 = هامش ربح أعلى)')
        parser.add_argument('--dry-run', action='store_true', help='نفّذ ثم تراجع عن كل التغييرات (اختبار)')

    def handle(self, *args, **options):
        from apps.core.models import Tenant

        tenant = self._resolve_tenant(Tenant, options['tenant'])
        self.days = round(options['months'] * 30.4) if options['months'] else options['days']
        self.months = max(1, round(self.days / 30.4))
        self.scale = max(1.0, self.days / 7)
        self.profit_boost = Decimal(str(options['profit_boost']))
        self.today = date.today()
        self.rng = random.Random(42)
        self.kit_item = None
        self._baseline = None
        self._restock_seq = 0

        self.user = self._resolve_user(tenant)
        self.tenant = tenant
        self.main_stock, self.second_stock = self._resolve_stocks(tenant)
        self.main_treasury, self.usd_treasury = self._resolve_treasuries(tenant)

        self._baseline = self._totals()
        self.stdout.write(f'⏳ إضافة بيانات تجريبية شاملة على «{tenant.name}» لآخر {self.days} يوم (scale={self.scale:.1f})...')

        class _Rollback(Exception):
            pass

        try:
            with transaction.atomic():
                self._run_all()
                self._print_summary()
                if options['dry_run']:
                    self._coverage_report()
                    raise _Rollback()
        except _Rollback:
            self.stdout.write(self.style.WARNING('↩ --dry-run: تم التراجع عن كل التغييرات.'))
            return
        self.stdout.write(self.style.SUCCESS('✅ اكتمل! افتح لوحة التحكم لمشاهدة النشاط.'))

    def _run_all(self):
        self.categories = self._ensure_categories()
        self.units = self._ensure_units()
        self.customers = self._ensure_customers()
        self.suppliers = self._ensure_suppliers()
        self.items = self._ensure_items()
        self.bank_accounts = self._ensure_bank_accounts()
        self.agents = self._ensure_agents()
        self.insurance_companies = self._ensure_insurance_companies()
        self.insurance_members = self._ensure_insurance_members()
        self.extra_users = self._ensure_extra_users_and_groups()
        self._ensure_branches()
        self._seed_opening_balances()

        self._create_purchases()
        self._create_purchase_returns()
        self._create_purchase_rfqs()

        self._ensure_kit()
        self._create_manufacturing_orders()

        self.sale_invoices = self._create_sales()
        self._create_extra_customer_payments()
        self._create_sale_returns()
        self._create_sale_quotes()
        self._create_insurance_sales_and_claims()

        self._create_expenses()
        self._create_employees_payroll()

        self._create_treasury_transfers()
        self._create_bank_transfers()

        self._create_agent_invoice_requests()

        self._create_stock_transfers()
        self._create_stocktakes()
        self._create_stock_destructions()

        self._create_store_orders()
        self._create_notifications()
        self._create_support_tickets()
        self._create_exchange_rate_history()
        self._create_activity_log()
        self._backdate_created_at()

    # ── helpers عامة ──────────────────────────────────────────────────────
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

    def _resolve_stocks(self, tenant):
        from apps.stocks.models import Stock
        main = Stock.objects.filter(tenant=tenant, is_default=True).first() \
            or Stock.objects.filter(tenant=tenant).first()
        second = Stock.objects.filter(tenant=tenant).exclude(pk=main.pk).first()
        if not main:
            raise CommandError('لا يوجد مخزن لهذا المشترك')
        return main, second

    def _resolve_treasuries(self, tenant):
        from apps.treasury.models import Treasury
        main = Treasury.objects.filter(tenant=tenant, is_default=True).first() \
            or Treasury.objects.filter(tenant=tenant).first()
        usd = Treasury.objects.filter(tenant=tenant).exclude(pk=main.pk).first()
        if not main:
            raise CommandError('لا توجد خزينة لهذا المشترك')
        return main, usd

    def _spread_day(self, i, total):
        days_ago = int(i * self.days / max(total, 1))
        days_ago = min(days_ago, self.days - 1)
        return self.today - timedelta(days=days_ago)

    def _n(self, base):
        """يضاعف عدد السجلات الأساسي (لأسبوع) تناسبياً مع طول الفترة."""
        return max(base, round(base * self.scale))

    def _per_month(self, base_week, per_month):
        """للأسبوع الواحد `base_week` سجل؛ وللفترات الطويلة `per_month` سجل في الشهر."""
        return base_week if self.scale <= 1 else per_month * self.months

    def _monthly_dates(self, day):
        """تواريخ اليوم `day` من كل شهر تقع ضمن الفترة (من أقدم شهر حتى الحالي)."""
        start = self.today - timedelta(days=self.days)
        y, m = start.year, start.month
        out = []
        while (y, m) <= (self.today.year, self.today.month):
            d = date(y, m, min(day, 28))
            if start <= d <= self.today:
                out.append(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out

    def _growth(self, d):
        """معامل نمو تدريجي: الأشهر الأحدث أنشط قليلاً (0.8 → 1.2) — يُعطي اتجاهاً صاعداً واقعياً."""
        if self.scale <= 1:
            return 1.0
        age = (self.today - d).days / max(self.days, 1)
        return 1.2 - 0.4 * min(max(age, 0), 1)

    def _qty_for_value(self, price, low, high, max_qty, on=None):
        price = float(price) or 1.0
        g = self._growth(on) if on else 1.0
        target = self.rng.uniform(low, high) * g
        qty = max(1, round(target / price))
        return Decimal(str(min(qty, max_qty)))

    def _safe(self, label, fn):
        """ينفّذ fn() داخل نقطة حفظ (savepoint) مستقلة — فشل سجل واحد لا يُسقط البقية."""
        try:
            with transaction.atomic():
                return fn()
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'    ⚠ {label}: {e}'))
            return None

    def _backdate(self, model, pk, field, dt_value):
        model.objects.filter(pk=pk).update(**{field: dt_value})

    # ── تصنيفات ووحدات ────────────────────────────────────────────────────
    def _ensure_categories(self):
        from apps.items.models import Category
        cats = []
        for n in CATEGORIES:
            cat, _ = Category.objects.get_or_create(
                tenant=self.tenant, name=n, defaults=dict(is_active=True, created_by=self.user),
            )
            cats.append(cat)
        self.stdout.write(f'  ✓ تصنيفات: {len(cats)}')
        return cats

    def _ensure_units(self):
        from apps.items.models import Unit
        units = []
        for name, abbr in UNITS:
            u, _ = Unit.objects.get_or_create(
                tenant=self.tenant, name=name,
                defaults=dict(abbreviation=abbr, is_active=True, created_by=self.user),
            )
            units.append(u)
        self.stdout.write(f'  ✓ وحدات قياس: {len(units)}')
        return units

    # ── أصناف ─────────────────────────────────────────────────────────────
    def _set_hc_prices(self, item):
        # في وضع العملة الصعبة السعر المحلي مُشتق من سعر بالعملة الصعبة —
        # بدون هذا الحقل لن تُعاد تسعير الصنف أبداً عند تغيير سعر الصرف
        # لاحقاً (راجع apps/items/services.py: reprice_items_for_rate).
        if self.tenant.hard_currency_mode and self.tenant.exchange_rate:
            rate = Decimal(str(self.tenant.exchange_rate))
            item.cost_price_hc = (item.cost_price / rate).quantize(Decimal('0.0001'))
            item.selling_price_hc = (item.selling_price / rate).quantize(Decimal('0.0001'))
            item.min_selling_price_hc = (item.min_selling_price / rate).quantize(Decimal('0.0001'))

    def _ensure_items(self):
        from apps.items.models import Item, ItemUnit
        items = list(Item.objects.filter(tenant=self.tenant))
        existing = {i.name for i in items}
        created = 0
        for idx, (name, cat_idx, cost, sell, unit_idx, track_batch, prefix,
                  generic, manuf, country, dosage) in enumerate(ITEMS_DATA):
            if name in existing:
                continue
            cost_price = Decimal(str(cost))
            selling_price = (Decimal(str(sell)) * self.profit_boost).quantize(Decimal('1'))
            min_selling_price = Decimal(str(int(cost * 1.05)))
            item = Item(
                tenant=self.tenant, name=name, category=self.categories[cat_idx],
                unit=self.units[unit_idx], item_type='product',
                cost_price=cost_price, selling_price=selling_price,
                min_selling_price=min_selling_price,
                sku=f'{prefix}-{idx+1:03d}', supplier=self.suppliers[idx % len(self.suppliers)],
                track_batch=track_batch, track_expiry=track_batch,
                generic_name=generic, manufacturer=manuf, country_of_origin=country,
                dosage_form=dosage, min_quantity=Decimal('50'), max_quantity=Decimal('5000'),
                is_active=True, is_sellable=True, is_purchasable=True, created_by=self.user,
            )
            self._set_hc_prices(item)
            item.save()
            items.append(item)
            created += 1
            if idx % 4 == 0:
                ItemUnit.objects.get_or_create(tenant=self.tenant, item=item, name='قطعة', defaults=dict(factor=Decimal('1')))
                ItemUnit.objects.get_or_create(tenant=self.tenant, item=item, name='كرتون', defaults=dict(factor=Decimal('12')))
        self.stdout.write(f'  ✓ أصناف: {created} جديد (إجمالي {len(items)})')
        return items

    # ── عملاء وموردون ─────────────────────────────────────────────────────
    def _ensure_customers(self):
        from apps.customers.models import Customer
        customers = list(Customer.objects.filter(tenant=self.tenant))
        existing = {c.name for c in customers}
        created = 0
        boost = 3 if self.scale > 1 else 1
        for name, phone, city, cl in CUSTOMERS:
            if name in existing:
                if boost > 1 and cl:
                    Customer.objects.filter(tenant=self.tenant, name=name, credit_limit__lt=cl * boost).update(
                        credit_limit=Decimal(str(cl * boost)))
                continue
            c = Customer.objects.create(
                tenant=self.tenant, name=name, phone=phone, city=city,
                credit_limit=Decimal(str(cl * boost)), created_by=self.user,
            )
            customers.append(c)
            created += 1
        self.stdout.write(f'  ✓ عملاء: {created} جديد (إجمالي {len(customers)})')
        return customers

    def _ensure_suppliers(self):
        from apps.suppliers.models import Supplier
        suppliers = list(Supplier.objects.filter(tenant=self.tenant))
        existing = {s.name for s in suppliers}
        created = 0
        for name, phone, city in SUPPLIERS:
            if name in existing:
                continue
            s = Supplier.objects.create(tenant=self.tenant, name=name, phone=phone, city=city, created_by=self.user)
            suppliers.append(s)
            created += 1
        self.stdout.write(f'  ✓ موردون: {created} جديد (إجمالي {len(suppliers)})')
        return suppliers

    # ── حسابات بنكية ──────────────────────────────────────────────────────
    def _ensure_bank_accounts(self):
        from apps.bank_accounts.models import BankAccount
        accounts = []
        for i, (name, bank_name, acc_no, currency, is_default) in enumerate(BANK_ACCOUNTS):
            acc, _ = BankAccount.objects.get_or_create(
                tenant=self.tenant, name=name,
                defaults=dict(
                    bank_name=bank_name, account_number=acc_no, currency=currency,
                    is_default=(i == 0), is_active=True, created_by=self.user,
                ),
            )
            accounts.append(acc)
        self.stdout.write(f'  ✓ حسابات بنكية: {len(accounts)}')
        return accounts

    # ── مناديب ────────────────────────────────────────────────────────────
    def _ensure_agents(self):
        from apps.agents.models import Agent
        agents = list(Agent.objects.filter(tenant=self.tenant))
        existing = {a.name for a in agents}
        created = 0
        for name, phone, city in AGENTS:
            if name in existing:
                continue
            a = Agent.objects.create(
                tenant=self.tenant, name=name, phone=phone, city=city,
                commission_type='percentage', commission_basis='both',
                commission_rate=Decimal('2.5'), commission_rate_collection=Decimal('1.0'),
                is_active=True, created_by=self.user,
            )
            agents.append(a)
            created += 1
        self.stdout.write(f'  ✓ مناديب: {created} جديد (إجمالي {len(agents)})')
        return agents

    # ── تأمين ─────────────────────────────────────────────────────────────
    def _ensure_insurance_companies(self):
        from apps.insurance.models import InsuranceCompany
        companies = []
        for name, coverage in INSURANCE_COMPANIES:
            c, _ = InsuranceCompany.objects.get_or_create(
                tenant=self.tenant, name=name,
                defaults=dict(
                    default_coverage_percent=Decimal(str(coverage)),
                    settlement_period_days=30, is_active=True, created_by=self.user,
                ),
            )
            companies.append(c)
        self.stdout.write(f'  ✓ شركات تأمين: {len(companies)}')
        return companies

    def _ensure_insurance_members(self):
        from apps.insurance.models import InsuranceMember
        from apps.insurance.services import quick_register_card
        names = [
            'عبدالله محمد الطيب', 'فاطمة الزهراء أحمد', 'مريم عثمان الحسن',
            'إبراهيم كمال الدين', 'زينب الفاتح آدم', 'حسن النور بابكر',
            'آمنة الصديق يوسف', 'عثمان إدريس محمد',
        ]
        members = list(InsuranceMember.objects.filter(tenant=self.tenant))
        existing_names = {m.full_name for m in members}
        created = 0
        for i, name in enumerate(names):
            if name in existing_names:
                continue
            company = self.insurance_companies[i % len(self.insurance_companies)]
            result = self._safe(f'عضو تأمين {name}', lambda c=company, n=name, i=i: quick_register_card(
                self.tenant,
                {'insurance_company_id': c.id, 'card_number': f'CARD-{2026}-{i+1:04d}', 'full_name': n},
                self.user,
            ))
            if result:
                members.append(result['member'])
                created += 1
        self.stdout.write(f'  ✓ مشتركو تأمين: {created} جديد (إجمالي {len(members)})')
        return members

    # ── أرصدة افتتاحية ────────────────────────────────────────────────────
    def _seed_opening_balances(self):
        """
        بدون رصيد افتتاحي، أول عملية صرف (شراء/مصروف/راتب) تفشل لأن الخزينة
        والحسابات البنكية تبدأ من صفر — نضبط رصيداً افتتاحياً واقعياً قبل يوم
        بداية الأسبوع التجريبي حتى تنجح عمليات الصرف طوال الأسبوع.
        """
        from apps.treasury.services import set_opening_balance as set_treasury_opening
        from apps.bank_accounts.services import set_opening_balance as set_bank_opening

        opening_date = self.today - timedelta(days=self.days)
        k = Decimal(str(round(self.scale, 2)))
        self._safe('رصيد افتتاحي — الخزينة الرئيسية', lambda: set_treasury_opening(
            self.tenant, self.main_treasury, Decimal('20000000') * k, opening_date, user=self.user,
        ))
        if self.usd_treasury:
            self._safe('رصيد افتتاحي — خزينة العملة الصعبة', lambda: set_treasury_opening(
                self.tenant, self.usd_treasury, Decimal('2000'), opening_date, user=self.user,
            ))
        for i, acc in enumerate(self.bank_accounts):
            amount = (Decimal('60000000') if i == 0 else Decimal('5000000')) * k
            self._safe(f'رصيد افتتاحي — {acc.name}', lambda acc=acc, amount=amount: set_bank_opening(
                self.tenant, acc, amount, opening_date, user=self.user,
            ))
        self.stdout.write('  ✓ أرصدة افتتاحية للخزينة والحسابات البنكية')

    # ── فروع ──────────────────────────────────────────────────────────────
    def _ensure_branches(self):
        from apps.core.models import Branch
        if self.scale <= 1:
            return
        data = [('الفرع الرئيسي — الخرطوم', 'KHT', 'الخرطوم — شارع الجمهورية', '0912300001', True),
                ('فرع أمدرمان', 'OMD', 'أمدرمان — سوق ليبيا', '0912300002', False)]
        branches = []
        for name, code, addr, phone, is_def in data:
            b, _ = Branch.objects.get_or_create(
                tenant=self.tenant, name=name,
                defaults=dict(code=code, address=addr, phone=phone, is_default=is_def, is_active=True),
            )
            branches.append(b)
        for stock, branch in zip([self.main_stock, self.second_stock], branches):
            if stock and not stock.branch_id:
                stock.branch = branch
                stock.save(update_fields=['branch'])
        self.stdout.write(f'  ✓ فروع: {len(branches)}')

    # ── تصنيع (طقم إسعافات مجمَّع من أصناف قائمة) ─────────────────────────
    def _ensure_kit(self):
        from apps.items.models import Item, BOMRecipe, BOMLine
        if self.scale <= 1:
            return
        by_name = {i.name: i for i in self.items}
        comps = [('ضمادات وشاش طبي — كرتون', '0.2'), ('قفازات طبية لاتكس — كرتون', '0.1'),
                 ('معقم يدين 500 مل — كرتون', '0.1'), ('ترمومتر رقمي — كرتون 50 قطعة', '0.02')]
        comp_items = [(by_name[n], Decimal(q)) for n, q in comps if n in by_name]
        if len(comp_items) < 2:
            self.stdout.write('  ⚠ مكوّنات الطقم غير متوفرة — تخطي التصنيع')
            return
        name = 'طقم إسعافات أولية منزلي'
        kit = by_name.get(name)
        if kit is None:
            cost = (sum(ci.cost_price * q for ci, q in comp_items) * Decimal('1.05')).quantize(Decimal('1'))
            sell = (cost * Decimal('1.6') * self.profit_boost).quantize(Decimal('1'))
            kit = Item(
                tenant=self.tenant, name=name, category=self.categories[1], unit=self.units[6],
                item_type='product', cost_price=cost, selling_price=sell,
                min_selling_price=(cost * Decimal('1.05')).quantize(Decimal('1')),
                sku='KIT-001', supplier=self.suppliers[0], min_quantity=Decimal('20'),
                max_quantity=Decimal('2000'), is_active=True, is_sellable=True,
                is_purchasable=False, created_by=self.user,
            )
            self._set_hc_prices(kit)
            kit.save()
            self.items.append(kit)
        recipe, created = BOMRecipe.objects.get_or_create(
            tenant=self.tenant, item=kit, defaults=dict(notes='تجميع طقم إسعافات من أصناف المخزن', is_active=True),
        )
        if created:
            for comp, q in comp_items:
                BOMLine.objects.create(tenant=self.tenant, recipe=recipe, component=comp, quantity=q)
        self.kit_item, self.kit_recipe = kit, recipe
        self.stdout.write(f'  ✓ وصفة تصنيع: {kit.name} ({len(comp_items)} مكوّن)')

    def _produce_kits(self, qty, on_date, leave_draft=False):
        from apps.stocks.models import ManufacturingOrder
        from apps.stocks.services import confirm_manufacturing_order
        qty = Decimal(str(int(qty) + 1))
        for line in self.kit_recipe.lines.select_related('component'):
            self._ensure_stock(line.component, line.quantity * qty, on_date)
        order = ManufacturingOrder.objects.create(
            tenant=self.tenant, recipe=self.kit_recipe, stock=self.main_stock, quantity=qty,
            order_date=on_date, status='draft', notes='تجميع دفعة أطقم إسعافات', 
        )
        if not leave_draft:
            confirm_manufacturing_order(order)
        return order

    def _create_manufacturing_orders(self):
        if self.kit_item is None:
            return
        n = self.months * 3
        created = 0
        for i in range(n):
            d = self._spread_day(i, n)
            draft = (i % 9 == 4)
            if self._safe(f'أمر تصنيع #{i+1}', lambda d=d, draft=draft: self._produce_kits(
                self.rng.randint(40, 120), d, leave_draft=draft,
            )) is not None:
                created += 1
        self.stdout.write(f'  ✓ أوامر تصنيع: {created}/{n}')

    # ── مستخدمون إضافيون وصلاحيات ─────────────────────────────────────────
    def _ensure_extra_users_and_groups(self):
        from apps.accounts.models import PermissionGroup
        from apps.accounts.permissions import get_permission_keys

        staff = [
            ('cashier1', 'كاشير المبيعات', 'محمد'),
            ('accountant1', 'محاسب الشركة', 'منى'),
            ('warehouse1', 'أمين المخزن', 'أحمد'),
        ]
        users = []
        for username, role, first_name in staff:
            u, created = User.objects.get_or_create(
                username=username, tenant=self.tenant,
                defaults=dict(first_name=first_name, last_name=role, is_active=True, is_tenant_admin=False),
            )
            if created:
                u.set_password('Demo@1234')
                u.save(update_fields=['password'])
            users.append(u)

        all_keys = list(get_permission_keys())

        def _group(name, contains):
            keys = [k for k in all_keys if any(s in k for s in contains)]
            g, _ = PermissionGroup.objects.get_or_create(
                tenant=self.tenant, name=name,
                defaults=dict(permissions={k: True for k in keys}, is_active=True),
            )
            return g

        cashier_group = _group('كاشير المبيعات', ['sale', 'customer'])
        accountant_group = _group('محاسب', ['expense', 'treasury', 'bank', 'report'])
        warehouse_group = _group('أمين مخزن', ['stock', 'purchase', 'item'])

        if len(users) == 3:
            cashier_group.users.add(users[0])
            accountant_group.users.add(users[1])
            warehouse_group.users.add(users[2])

        self.stdout.write(f'  ✓ مستخدمون إضافيون: {len(users)} | مجموعات صلاحيات: 3')
        return users

    # ── مشتريات ───────────────────────────────────────────────────────────
    def _create_purchases(self):
        from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine
        from apps.purchases.services import confirm_purchase_invoice

        # الفترات الطويلة: دفعة مشتريات أولية فقط — والباقي يُعاد توريده عند الحاجة أثناء البيع (_restock)
        # فيبقى حجم المشتريات قريباً من تكلفة ما بيع فعلاً ويبقى الربح عالياً
        n_invoices = 16 if self.scale <= 1 else round(16 * self.scale * 0.3)
        payment_cycle = ['bank', 'credit', 'bank', 'credit', 'cash', 'bank', 'mixed']
        confirmed = 0
        # أول تمريرة: كل الأصناف تُشترى مرة على الأقل لضمان وجود رصيد كافٍ للبيع
        chunks = [self.items[i:i + 3] for i in range(0, len(self.items), 3)]

        for i in range(n_invoices):
            inv_date = self._spread_day(i, n_invoices)
            supplier = self.suppliers[i % len(self.suppliers)]
            pm = payment_cycle[i % len(payment_cycle)]
            line_items = chunks[i % len(chunks)] if i < len(chunks) else self.rng.sample(self.items, k=min(4, len(self.items)))

            def _build(inv_date=inv_date, supplier=supplier, pm=pm, line_items=line_items, i=i):
                invoice = PurchaseInvoice(
                    tenant=self.tenant, supplier=supplier, stock=self.main_stock,
                    invoice_date=inv_date, status='draft', payment_method=pm,
                    created_by=self.user,
                )
                if pm == 'bank':
                    invoice.bank_reference = f'TRF-{inv_date.strftime("%y%m%d")}-{i+1:02d}'
                    invoice.bank_account = self.bank_accounts[0]
                elif pm == 'mixed':
                    invoice.bank_reference = f'MIX-{inv_date.strftime("%y%m%d")}-{i+1:02d}'
                    invoice.bank_account = self.bank_accounts[0]
                invoice.save()

                subtotal = Decimal('0')
                expiry = inv_date + timedelta(days=self.rng.randint(365, 730))
                for item in line_items:
                    cost = item.cost_price or Decimal('10')
                    qty = self._qty_for_value(cost, 300000, 900000, max_qty=3000, on=inv_date)
                    line_total = cost * qty
                    subtotal += line_total
                    PurchaseInvoiceLine.objects.create(
                        tenant=self.tenant, invoice=invoice, item=item, quantity=qty,
                        unit_cost=cost, line_total=line_total,
                        batch_number=f'{item.sku or "BATCH"}-{inv_date.strftime("%y%m%d")}-{i + 1}',
                        expiry_date=expiry, created_by=self.user,
                    )
                if pm == 'mixed':
                    invoice.cash_amount = (subtotal * Decimal('0.4')).quantize(Decimal('0.01'))
                    invoice.bank_amount = subtotal - invoice.cash_amount
                invoice.subtotal = subtotal
                invoice.grand_total = subtotal
                invoice.save(update_fields=['subtotal', 'grand_total', 'cash_amount', 'bank_amount'])
                confirm_purchase_invoice(invoice, self.user)
                return invoice

            if self._safe(f'شراء ({inv_date})', _build) is not None:
                confirmed += 1

        self.stdout.write(f'  ✓ فواتير شراء: {confirmed}/{n_invoices}')

    def _create_purchase_returns(self):
        from apps.purchases.models import PurchaseInvoice, PurchaseReturn, PurchaseReturnLine
        from apps.purchases.services import confirm_purchase_return

        n_ret = self._per_month(4, 3)
        invoices = list(PurchaseInvoice.objects.filter(tenant=self.tenant, status='confirmed')[:n_ret + 2])
        created = 0
        for i, invoice in enumerate(invoices[:n_ret]):
            line = invoice.lines.first()
            if not line:
                continue
            ret_date = self._spread_day(i, n_ret)

            def _build(invoice=invoice, line=line, ret_date=ret_date):
                pr = PurchaseReturn.objects.create(
                    tenant=self.tenant, return_date=ret_date, original_invoice=invoice,
                    status='draft', refund_method='balance', reason='صنف زائد عن الحاجة',
                    created_by=self.user,
                )
                qty = min(line.returnable_quantity, Decimal('2'))
                if qty <= 0:
                    raise ValueError('لا توجد كمية قابلة للإرجاع')
                PurchaseReturnLine.objects.create(
                    tenant=self.tenant, purchase_return=pr, invoice_line=line, item=line.item,
                    returned_quantity=qty, unit_cost=line.unit_cost, created_by=self.user,
                )
                confirm_purchase_return(pr, self.user)
                return pr

            if self._safe(f'مرتجع شراء #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ مرتجعات شراء: {created}')

    def _create_purchase_rfqs(self):
        from apps.purchases.models import PurchaseRFQ, PurchaseRFQLine
        statuses = ['draft', 'sent', 'received', 'accepted', 'rejected', 'sent', 'draft', 'received']
        statuses = (statuses * self._n(1))[:self._per_month(len(statuses), 6)]
        created = 0
        for i, status in enumerate(statuses):
            rfq_date = self._spread_day(i, len(statuses))

            def _build(status=status, rfq_date=rfq_date, i=i):
                rfq = PurchaseRFQ.objects.create(
                    tenant=self.tenant, rfq_date=rfq_date,
                    expiry_date=rfq_date + timedelta(days=14),
                    supplier=self.suppliers[i % len(self.suppliers)], stock=self.main_stock,
                    status=status, notes='طلب عرض أسعار دوري', created_by=self.user,
                )
                for item in self.rng.sample(self.items, k=min(3, len(self.items))):
                    quoted = item.cost_price if status in ('received', 'accepted', 'rejected') else Decimal('0')
                    PurchaseRFQLine.objects.create(
                        tenant=self.tenant, rfq=rfq, item=item,
                        requested_quantity=Decimal(str(self.rng.randint(50, 300))),
                        quoted_price=quoted, created_by=self.user,
                    )
                rfq.recalculate_total()
                return rfq

            if self._safe(f'طلب عرض سعر #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ طلبات عروض أسعار: {created}')

    # ── مبيعات ────────────────────────────────────────────────────────────
    def _create_sales(self):
        from apps.sales.models import SaleInvoice, SaleInvoiceLine
        from apps.sales.services import confirm_sale_invoice

        n_invoices = self._n(42)
        payment_cycle = ['cash', 'bank', 'credit', 'cash', 'mixed', 'bank', 'credit']
        confirmed_invoices = []

        for i in range(n_invoices):
            inv_date = self._spread_day(i, n_invoices)
            pm = payment_cycle[i % len(payment_cycle)]
            customer = self.customers[i % len(self.customers)] if pm in ('credit', 'mixed') or i % 3 == 0 else None
            agent = self.agents[i % len(self.agents)] if i % 2 == 0 else None
            line_items = self.rng.sample(self.items, k=min(self.rng.choice([1, 2, 2, 3]), len(self.items)))

            def _build(inv_date=inv_date, pm=pm, customer=customer, agent=agent, line_items=line_items, i=i):
                invoice = SaleInvoice(
                    tenant=self.tenant, customer=customer, stock=self.main_stock,
                    invoice_date=inv_date, status='draft', payment_method=pm,
                    delivery_type='immediate', agent=agent, created_by=self.user,
                )
                if pm in ('bank', 'mixed'):
                    invoice.bank_reference = f'BTR-{inv_date.strftime("%y%m%d")}-{i+1:02d}'
                    invoice.bank_account = self.bank_accounts[0]
                invoice.save()

                subtotal = Decimal('0')
                for item in line_items:
                    price = item.selling_price or Decimal('10')
                    cost = item.cost_price or Decimal('0')
                    qty = self._qty_for_value(price, 150000, 650000, max_qty=800, on=inv_date)
                    line_total = price * qty
                    subtotal += line_total
                    SaleInvoiceLine.objects.create(
                        tenant=self.tenant, invoice=invoice, item=item, quantity=qty,
                        unit_price=price, cost_price_snapshot=cost,
                        discount_amount=Decimal('0'), line_total=line_total,
                        created_by=self.user,
                    )
                if pm == 'mixed':
                    invoice.cash_amount = (subtotal * Decimal('0.5')).quantize(Decimal('0.01'))
                    invoice.bank_amount = subtotal - invoice.cash_amount
                invoice.subtotal = subtotal
                invoice.grand_total = subtotal
                invoice.save(update_fields=['subtotal', 'grand_total', 'cash_amount', 'bank_amount'])
                self._ensure_stock_for_invoice(invoice, inv_date)
                confirm_sale_invoice(invoice, self.user)
                return invoice

            invoice = self._safe(f'بيع ({inv_date})', _build)
            if invoice is None and pm == 'credit' and self.scale > 1:
                # العميل تجاوز حده الائتماني — يُحوَّل البيع إلى نقدي بدل إسقاطه
                pm = 'cash'
                invoice = self._safe(f'بيع نقدي بديل ({inv_date})', lambda: _build(pm='cash'))
            if invoice is not None:
                confirmed_invoices.append(invoice)
                # الفترات الطويلة: العملاء يسدّدون الفواتير الآجلة بعد أيام (الغالبية) —
                # هذا يُبقي أرصدتهم أقل من الحد الائتماني ويجعل الذمم واقعية.
                if self.scale > 1 and pm == 'credit' and customer and self.rng.random() < (0.55 if (self.today - inv_date).days < 20 else 0.93):
                    self._pay_credit_invoice(invoice, inv_date)

        self.stdout.write(f'  ✓ فواتير بيع: {len(confirmed_invoices)}/{n_invoices}')
        return confirmed_invoices

    # ── إعادة التوريد عند الحاجة ────────────────────────────────────────
    def _ensure_stock(self, item, qty, on_date):
        """لو رصيد المخزن الرئيسي لا يكفي `qty` يُنشأ توريد (أو أمر تصنيع) قبل تاريخ العملية."""
        from apps.stocks.models import StockQuantity
        if self.scale <= 1:
            return
        sq = StockQuantity.objects.filter(tenant=self.tenant, stock=self.main_stock, item=item).first()
        available = (sq.quantity - sq.reserved_quantity) if sq else Decimal('0')
        qty = Decimal(str(qty))
        if available >= qty:
            return
        shortfall = qty - available
        d = max(on_date - timedelta(days=self.rng.randint(1, 3)), self.today - timedelta(days=self.days))
        if self.kit_item is not None and item.pk == self.kit_item.pk:
            self._produce_kits(max(shortfall, Decimal('60')), d)
        else:
            self._restock(item, shortfall, d)

    def _ensure_stock_for_invoice(self, invoice, on_date):
        for line in invoice.lines.select_related('item'):
            self._ensure_stock(line.item, line.quantity * (line.unit_factor or Decimal('1')), on_date)

    def _restock(self, item, shortfall, on_date):
        from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine
        from apps.purchases.services import confirm_purchase_invoice
        cost = item.cost_price or Decimal('10')
        target = self.rng.uniform(800000, 2500000) * self._growth(on_date)
        qty = max(shortfall * Decimal('1.2'), Decimal(str(max(1, round(target / float(cost))))))
        qty = Decimal(str(int(qty) + 1))
        self._restock_seq += 1
        pm = ['bank', 'credit', 'bank', 'credit', 'cash', 'bank', 'mixed'][self._restock_seq % 7]
        supplier = item.supplier or self.suppliers[self._restock_seq % len(self.suppliers)]
        invoice = PurchaseInvoice(
            tenant=self.tenant, supplier=supplier, stock=self.main_stock,
            invoice_date=on_date, status='draft', payment_method=pm, created_by=self.user,
        )
        if pm in ('bank', 'mixed'):
            invoice.bank_reference = f'RST-{on_date.strftime("%y%m%d")}-{self._restock_seq}'
            invoice.bank_account = self.bank_accounts[0]
        invoice.save()
        total = cost * qty
        PurchaseInvoiceLine.objects.create(
            tenant=self.tenant, invoice=invoice, item=item, quantity=qty, unit_cost=cost, line_total=total,
            batch_number=f'{item.sku or "BATCH"}-{on_date.strftime("%y%m%d")}-R{self._restock_seq}',
            expiry_date=on_date + timedelta(days=self.rng.randint(365, 730)), created_by=self.user,
        )
        if pm == 'mixed':
            invoice.cash_amount = (total * Decimal('0.4')).quantize(Decimal('0.01'))
            invoice.bank_amount = total - invoice.cash_amount
        invoice.subtotal = total
        invoice.grand_total = total
        invoice.save(update_fields=['subtotal', 'grand_total', 'cash_amount', 'bank_amount'])
        confirm_purchase_invoice(invoice, self.user)

    def _pay_credit_invoice(self, invoice, inv_date):
        from apps.sales.services import record_customer_payment
        pay_date = min(self.today, inv_date + timedelta(days=self.rng.randint(4, 35)))
        fraction = Decimal(str(self.rng.choice([1, 1, 1, 1, 0.75, 0.5])))
        amount = (invoice.grand_total * fraction).quantize(Decimal('0.01'))
        if amount <= 0:
            return
        method = self.rng.choice(['cash', 'bank'])
        kwargs = dict(treasury=self.main_treasury) if method == 'cash' else dict(bank_account=self.bank_accounts[0])
        self._safe(f'سداد فاتورة {invoice.pk}', lambda: record_customer_payment(
            invoice=invoice, amount=amount, method=method, date=pay_date, user=self.user, **kwargs,
        ))

    def _create_extra_customer_payments(self):
        from apps.sales.services import record_customer_payment
        credit_invoices = [inv for inv in self.sale_invoices if inv.payment_method == 'credit' and inv.customer_id][:self._n(6)]
        created = 0
        for i, inv in enumerate(credit_invoices):
            inv.refresh_from_db()
            amount = min((inv.grand_total * Decimal('0.4')).quantize(Decimal('0.01')), inv.remaining_amount)
            if amount <= 0:
                continue
            pay_date = self._spread_day(i, len(credit_invoices))
            method = 'cash' if i % 2 == 0 else 'bank'
            kwargs = dict(treasury=self.main_treasury) if method == 'cash' else dict(bank_account=self.bank_accounts[0])
            if self._safe(f'دفعة عميل #{i+1}', lambda inv=inv, amount=amount, pay_date=pay_date, method=method, kwargs=kwargs: record_customer_payment(
                invoice=inv, amount=amount, method=method, date=pay_date, user=self.user, **kwargs,
            )) is not None:
                created += 1
        self.stdout.write(f'  ✓ دفعات إضافية على فواتير آجلة: {created}')

    def _create_sale_returns(self):
        from apps.sales.models import SaleReturn, SaleReturnLine
        from apps.sales.services import confirm_sale_return

        n_ret = self._n(5)
        # مرتجعات موزّعة على الفترة كلها لا على أول الفواتير فقط
        candidates = [inv for inv in self.sale_invoices if inv.status == 'confirmed']
        candidates = candidates[::max(1, len(candidates) // max(n_ret, 1))]
        created = 0
        for i, invoice in enumerate(candidates[:n_ret]):
            line = invoice.lines.first()
            if not line:
                continue
            ret_date = max(invoice.invoice_date, self._spread_day(i, n_ret))
            refund_method = 'cash' if i % 2 == 0 else 'balance'

            def _build(invoice=invoice, line=line, ret_date=ret_date, refund_method=refund_method):
                sr = SaleReturn.objects.create(
                    tenant=self.tenant, return_date=ret_date, original_invoice=invoice,
                    status='draft', refund_method=refund_method, reason='رغبة العميل',
                    created_by=self.user,
                )
                qty = min(line.quantity, Decimal('1'))
                SaleReturnLine.objects.create(
                    tenant=self.tenant, sale_return=sr, invoice_line=line, item=line.item,
                    returned_quantity=qty, unit_price=line.unit_price,
                    line_total=(qty * line.unit_price), created_by=self.user,
                )
                confirm_sale_return(sr, self.user)
                return sr

            if self._safe(f'مرتجع بيع #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ مرتجعات بيع: {created}')

    def _create_sale_quotes(self):
        from apps.sales.services import (
            build_quote_from_post, mark_quote_sent, mark_quote_accepted,
            mark_quote_rejected, convert_quote_to_invoice,
        )
        from apps.sales.services import confirm_sale_invoice

        outcomes = ['converted', 'converted', 'rejected', 'sent', 'draft', 'converted', 'rejected', 'sent']
        outcomes = (outcomes * self._n(1))[:self._per_month(len(outcomes), 10)]
        created = 0
        for i, outcome in enumerate(outcomes):
            q_date = self._spread_day(i, len(outcomes))
            customer = self.customers[i % len(self.customers)]
            line_items = self.rng.sample(self.items, k=min(2, len(self.items)))

            def _build(outcome=outcome, q_date=q_date, customer=customer, line_items=line_items):
                post_data = dict(
                    stock_id=self.main_stock.id, customer_id=customer.id,
                    quote_date=q_date, expiry_date=q_date + timedelta(days=15),
                    reference_number='', notes='', terms='', quote_discount_type='percent',
                    quote_discount_value=0,
                )
                lines_data = [
                    dict(item_id=item.id, quantity=self.rng.randint(5, 30), unit_price=item.selling_price)
                    for item in line_items
                ]
                quote = build_quote_from_post(self.tenant, self.user, post_data, lines_data)
                if outcome == 'draft':
                    return quote
                mark_quote_sent(quote, self.user)
                if outcome == 'sent':
                    return quote
                if outcome == 'rejected':
                    mark_quote_rejected(quote, self.user)
                    return quote
                mark_quote_accepted(quote, self.user)
                invoice = convert_quote_to_invoice(quote, self.user, payment_method='cash')
                invoice.invoice_date = q_date
                invoice.save(update_fields=['invoice_date'])
                self._ensure_stock_for_invoice(invoice, q_date)
                confirm_sale_invoice(invoice, self.user)
                return quote

            if self._safe(f'عرض سعر #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ عروض أسعار: {created}')

    def _create_insurance_sales_and_claims(self):
        from apps.sales.models import SaleInvoice, SaleInvoiceLine
        from apps.sales.services import confirm_sale_invoice
        from apps.insurance.services import create_claim_from_sale, submit_claim, record_claim_response, settle_claim_payment

        if not self.insurance_members:
            self.stdout.write('  ⚠ لا يوجد مشتركو تأمين — تخطي مطالبات التأمين')
            return

        n = self._n(6)
        response_cycle = ['approved', 'approved', 'partially_approved', 'approved', 'rejected', 'approved']
        claims_created = 0
        for i in range(n):
            inv_date = self._spread_day(i, n)
            member = self.insurance_members[i % len(self.insurance_members)]
            line_items = self.rng.sample(self.items, k=min(2, len(self.items)))
            coverage = member.effective_coverage_percent

            def _build(inv_date=inv_date, member=member, line_items=line_items, coverage=coverage, i=i):
                invoice = SaleInvoice(
                    tenant=self.tenant, customer=None, stock=self.main_stock,
                    invoice_date=inv_date, status='draft', payment_method='mixed',
                    delivery_type='immediate', insurance_member=member,
                    insurance_card_number=member.card_number, created_by=self.user,
                )
                invoice.save()
                subtotal = Decimal('0')
                for item in line_items:
                    price = item.selling_price or Decimal('10')
                    cost = item.cost_price or Decimal('0')
                    qty = self._qty_for_value(price, 80000, 250000, max_qty=200, on=inv_date)
                    line_total = price * qty
                    subtotal += line_total
                    SaleInvoiceLine.objects.create(
                        tenant=self.tenant, invoice=invoice, item=item, quantity=qty,
                        unit_price=price, cost_price_snapshot=cost,
                        discount_amount=Decimal('0'), line_total=line_total, created_by=self.user,
                    )
                insurance_amt = (subtotal * coverage / Decimal('100')).quantize(Decimal('0.01'))
                invoice.insurance_amount = insurance_amt
                invoice.cash_amount = subtotal - insurance_amt
                invoice.bank_amount = Decimal('0')
                invoice.subtotal = subtotal
                invoice.grand_total = subtotal
                invoice.save(update_fields=['subtotal', 'grand_total', 'cash_amount', 'bank_amount', 'insurance_amount'])
                self._ensure_stock_for_invoice(invoice, inv_date)
                confirm_sale_invoice(invoice, self.user)

                claim = create_claim_from_sale(invoice, self.tenant, coverage, self.user)
                if claim is None:
                    return invoice
                submit_claim(claim, self.user)
                status = response_cycle[i % len(response_cycle)]
                approved_amt = claim.covered_amount if status == 'approved' else (
                    Decimal('0') if status == 'rejected' else (claim.covered_amount * Decimal('0.6')).quantize(Decimal('0.01'))
                )
                record_claim_response(claim, approved_amt, status, 'تخفيض حسب سياسة الشركة' if status != 'approved' else '', self.user)
                if status != 'rejected' and approved_amt > 0:
                    settle_claim_payment(
                        claim, approved_amt, self._spread_day(i, n), f'SETTLE-{i+1}',
                        self.user, treasury=self.main_treasury, received_method='bank',
                    )
                return invoice

            if self._safe(f'بيع تأمين #{i+1}', _build) is not None:
                claims_created += 1
        self.stdout.write(f'  ✓ فواتير ومطالبات تأمين: {claims_created}/{n}')

    # ── مصروفات ───────────────────────────────────────────────────────────
    def _create_expenses(self):
        from apps.expenses.models import ExpenseCategory, Expense
        from apps.expenses.services import confirm_expense

        cats = {}
        for name in EXPENSE_CATEGORIES:
            cat, _ = ExpenseCategory.objects.get_or_create(tenant=self.tenant, name=name, defaults=dict(created_by=self.user))
            cats[name] = cat

        created = 0
        if self.scale > 1:
            plan = []
            for desc, cat_name, amount, pm, day, every in MONTHLY_EXPENSES:
                for k, d in enumerate(self._monthly_dates(day)):
                    if k % every == 0:
                        plan.append((desc, cat_name, amount, pm, d))
            weeks = self.days // 7
            for desc, cat_name, amount, pm in WEEKLY_EXPENSES:
                for w in range(weeks):
                    plan.append((desc, cat_name, amount, pm, self.today - timedelta(days=w * 7 + 1)))
            # تضخم/تذبذب طفيف في المبالغ
            plan = [(d_, c_, int(a_ * self.rng.uniform(0.9, 1.15)), p_, dt) for d_, c_, a_, p_, dt in plan]
        else:
            plan = [(desc, cat_name, amount, pm, self._spread_day(i, len(EXPENSES_DATA)))
                    for i, (desc, cat_name, amount, pm) in enumerate(EXPENSES_DATA)]

        for desc, cat_name, amount, pm, exp_date in plan:

            def _build(desc=desc, cat_name=cat_name, amount=amount, pm=pm, exp_date=exp_date):
                exp = Expense.objects.create(
                    tenant=self.tenant, category=cats[cat_name], description=desc,
                    amount=Decimal(str(amount)), expense_date=exp_date,
                    payment_method=pm, treasury=self.main_treasury if pm == 'cash' else None,
                    bank_account=self.bank_accounts[0] if pm == 'bank' else None,
                    status='draft', created_by=self.user,
                )
                confirm_expense(exp, self.user)
                return exp

            if self._safe(f'مصروف: {desc}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ مصروفات: {created}/{len(plan)}')

    # ── موظفون ────────────────────────────────────────────────────────────
    def _create_employees_payroll(self):
        from apps.employees.models import Employee
        from apps.employees.services import create_advance, create_incentive, create_salary_payment

        existing = {e.name for e in Employee.objects.filter(tenant=self.tenant)}
        employees = []
        for name, pos, dept, salary in EMPLOYEES:
            if name in existing:
                continue
            emp = Employee.objects.create(
                tenant=self.tenant, name=name, position=pos, department=dept,
                salary_type='fixed', base_salary=Decimal(str(salary)),
                hire_date=self.today - timedelta(days=self.days + self.rng.randint(60, 900)),
                is_active=True, created_by=self.user,
            )
            employees.append(emp)
        self.stdout.write(f'  ✓ موظفون جدد: {len(employees)}')

        if self.scale > 1:
            self._create_monthly_payroll(list(Employee.objects.filter(tenant=self.tenant, is_active=True)))
            return

        prev_month = self.today.month - 1 or 12
        prev_year = self.today.year if self.today.month > 1 else self.today.year - 1
        period_start = date(prev_year, prev_month, 1)
        period_end = min(self.today.replace(day=1) - timedelta(days=1), date(prev_year, prev_month, 28))

        advances_created = 0
        incentives_created = 0
        payrolls_created = 0
        for i, emp in enumerate(employees):
            adv_date = self._spread_day(i, len(employees))
            if i % 2 == 0:
                adv = self._safe(f'سلفة {emp.name}', lambda emp=emp, adv_date=adv_date: create_advance(
                    self.tenant, emp, Decimal(str(self.rng.randint(30000, 90000))), adv_date,
                    'cash', treasury=self.main_treasury, user=self.user,
                ))
                if adv:
                    advances_created += 1
                    sp = self._safe(f'كشف راتب {emp.name}', lambda emp=emp, adv=adv: create_salary_payment(
                        self.tenant, emp, period_start, period_end, emp.base_salary,
                        'cash', treasury=self.main_treasury, advance_ids=[adv.id], user=self.user,
                    ))
                    if sp:
                        self._safe(f'دفع راتب {emp.name}', lambda sp=sp: sp.pay())
                        payrolls_created += 1
            else:
                inc = self._safe(f'حافز {emp.name}', lambda emp=emp, adv_date=adv_date: create_incentive(
                    self.tenant, emp, Decimal(str(self.rng.randint(40000, 120000))),
                    'مكافأة أداء الأسبوع', 'bonus', 'immediate', 'cash',
                    treasury=self.main_treasury, date=adv_date, user=self.user,
                ))
                if inc:
                    incentives_created += 1
                sp = self._safe(f'كشف راتب {emp.name}', lambda emp=emp: create_salary_payment(
                    self.tenant, emp, period_start, period_end, emp.base_salary,
                    'bank', bank_account=self.bank_accounts[0], user=self.user,
                ))
                if sp:
                    self._safe(f'دفع راتب {emp.name}', lambda sp=sp: sp.pay())
                    payrolls_created += 1

        # إعادة محاولة دفع أي كشف راتب بقي "مسودة" (فشل الصرف وقتها لعدم كفاية
        # الرصيد) — بعد ضبط الأرصدة الافتتاحية يُفترض أن ينجح الآن.
        from apps.employees.models import EmployeeSalaryPayment
        retried = 0
        for sp in EmployeeSalaryPayment.objects.filter(tenant=self.tenant, status='draft'):
            self._safe(f'إعادة دفع راتب {sp.employee.name}', lambda sp=sp: sp.pay())
            sp.refresh_from_db()
            if sp.status == 'paid':
                retried += 1

        self.stdout.write(
            f'  ✓ سلف: {advances_created} | حوافز: {incentives_created} | '
            f'كشوف رواتب جديدة: {payrolls_created} | كشوف أُعيد دفعها: {retried}'
        )

    def _create_monthly_payroll(self, employees):
        """كشف راتب شهري لكل موظف لكل شهر مكتمل ضمن الفترة، مع سلف وحوافز دورية."""
        import calendar
        from apps.employees.services import create_advance, create_incentive, create_salary_payment
        from apps.employees.models import EmployeeSalaryPayment
        from apps.treasury.models import TreasuryMovement
        from apps.bank_accounts.models import BankAccountMovement

        start = self.today - timedelta(days=self.days)
        months = []
        y, m = start.year, start.month
        while (y, m) < (self.today.year, self.today.month):
            months.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

        paid = advances = incentives = 0
        for mi, (y, m) in enumerate(months):
            p_start = date(y, m, 1)
            p_end = date(y, m, calendar.monthrange(y, m)[1])
            pay_date = min(p_end, self.today)
            for i, emp in enumerate(employees):
                if emp.hire_date and emp.hire_date > p_end:
                    continue
                adv_ids, inc_ids = [], []
                if (mi + i) % 3 == 0:
                    adv = self._safe(f'سلفة {emp.name} {y}-{m}', lambda: create_advance(
                        self.tenant, emp, Decimal(str(self.rng.randint(30000, 120000))),
                        date(y, m, 10), 'cash', treasury=self.main_treasury, user=self.user,
                    ))
                    if adv:
                        adv_ids.append(adv.id)
                        advances += 1
                if (mi + i) % 4 == 1:
                    inc = self._safe(f'حافز {emp.name} {y}-{m}', lambda: create_incentive(
                        self.tenant, emp, Decimal(str(self.rng.randint(40000, 150000))),
                        'مكافأة أداء شهرية', 'bonus', 'with_salary', 'cash',
                        treasury=self.main_treasury, date=date(y, m, 25), user=self.user,
                    ))
                    if inc:
                        inc_ids.append(inc.id)
                        incentives += 1

                by_cash = (i % 2 == 0)
                kwargs = dict(treasury=self.main_treasury) if by_cash else dict(bank_account=self.bank_accounts[0])
                sp = self._safe(f'كشف راتب {emp.name} {y}-{m}', lambda: create_salary_payment(
                    self.tenant, emp, p_start, p_end, emp.base_salary,
                    'cash' if by_cash else 'bank', advance_ids=adv_ids, incentive_ids=inc_ids,
                    user=self.user, **kwargs,
                ))
                if not sp:
                    continue
                self._safe(f'دفع راتب {emp.name} {y}-{m}', sp.pay)
                sp.refresh_from_db()
                if sp.status != 'paid':
                    continue
                paid += 1
                # pay() يرحّل الحركة بتاريخ اليوم — نُرجعها لتاريخ نهاية الشهر
                if sp.treasury_movement_id:
                    TreasuryMovement.objects.filter(pk=sp.treasury_movement_id).update(movement_date=pay_date)
                if sp.bank_account_movement_id:
                    BankAccountMovement.objects.filter(pk=sp.bank_account_movement_id).update(movement_date=pay_date)
                EmployeeSalaryPayment.objects.filter(pk=sp.pk).update(
                    created_at=timezone.make_aware(datetime.combine(pay_date, dtime(hour=11))),
                )
        self.stdout.write(
            f'  ✓ كشوف رواتب مدفوعة: {paid} ({len(months)} شهر × {len(employees)} موظف) | '
            f'سلف: {advances} | حوافز: {incentives}'
        )

    # ── خزينة وبنوك ───────────────────────────────────────────────────────
    def _create_treasury_transfers(self):
        from apps.treasury.services import post_treasury_transfer
        if not self.usd_treasury:
            self.stdout.write('  ⚠ لا توجد خزينة ثانية — تخطي تحويلات الخزينة')
            return
        created = 0
        n = self._n(2)
        for i in range(n):
            t_date = self._spread_day(i, n)
            sdg_amount = Decimal(str(self.rng.randint(500000, 1500000)))
            usd_amount = (sdg_amount / self.tenant.exchange_rate).quantize(Decimal('0.01')) if self.tenant.exchange_rate else sdg_amount
            if self._safe(f'تحويل خزينة #{i+1}', lambda sdg_amount=sdg_amount, usd_amount=usd_amount, t_date=t_date: post_treasury_transfer(
                self.tenant, self.main_treasury, self.usd_treasury, sdg_amount, usd_amount,
                self.tenant.exchange_rate or 1, t_date, notes='تغذية خزينة العملة الصعبة', user=self.user,
            )) is not None:
                created += 1
        self.stdout.write(f'  ✓ تحويلات بين الخزائن: {created}')

    def _create_bank_transfers(self):
        from apps.bank_accounts.services import (
            post_bank_account_transfer, post_treasury_to_bank_transfer, post_bank_to_treasury_transfer,
        )
        created = 0
        if len(self.bank_accounts) >= 2:
            for i in range(self._n(2)):
                t_date = self._spread_day(i, self._n(2))
                amount = Decimal(str(self.rng.randint(300000, 900000)))
                if self._safe(f'تحويل بين بنوك #{i+1}', lambda amount=amount, t_date=t_date: post_bank_account_transfer(
                    self.tenant, self.bank_accounts[0], self.bank_accounts[1], amount, amount, 1, t_date,
                    notes='تسوية سيولة بين الحسابات', user=self.user,
                )) is not None:
                    created += 1

        for i in range(self._n(2)):
            t_date = self._spread_day(i, self._n(2))
            amount = Decimal(str(self.rng.randint(400000, 1200000)))
            if self._safe(f'خزينة→بنك #{i+1}', lambda amount=amount, t_date=t_date: post_treasury_to_bank_transfer(
                self.tenant, self.main_treasury, self.bank_accounts[0], amount, t_date,
                notes='إيداع نقدي بالبنك', user=self.user,
            )) is not None:
                created += 1

        for i in range(self._n(2)):
            t_date = self._spread_day(i, self._n(2))
            amount = Decimal(str(self.rng.randint(200000, 600000)))
            if self._safe(f'بنك→خزينة #{i+1}', lambda amount=amount, t_date=t_date: post_bank_to_treasury_transfer(
                self.tenant, self.bank_accounts[0], self.main_treasury, amount, t_date,
                notes='سحب نقدي من البنك', user=self.user,
            )) is not None:
                created += 1

        self.stdout.write(f'  ✓ تحويلات بنكية/خزينة-بنك: {created}')

    # ── طلبات فواتير المناديب ─────────────────────────────────────────────
    def _create_agent_invoice_requests(self):
        from apps.agents.models import AgentInvoiceRequest, AgentInvoiceRequestLine
        statuses = ['pending', 'pending', 'approved', 'approved', 'rejected', 'pending', 'approved', 'rejected']
        statuses = (statuses * self._n(1))[:self._per_month(len(statuses), 8)]
        created = 0
        for i, status in enumerate(statuses):
            agent = self.agents[i % len(self.agents)]
            customer = self.customers[i % len(self.customers)]
            line_items = self.rng.sample(self.items, k=min(2, len(self.items)))

            def _build(agent=agent, customer=customer, status=status, line_items=line_items, i=i):
                req = AgentInvoiceRequest.objects.create(
                    tenant=self.tenant, agent=agent, customer=customer,
                    customer_name=customer.name, customer_phone=customer.phone,
                    status=status, notes='طلب فاتورة من ميدان التوزيع', created_by=self.user,
                )
                subtotal = Decimal('0')
                for item in line_items:
                    qty = Decimal(str(self.rng.randint(5, 40)))
                    price = item.selling_price
                    AgentInvoiceRequestLine.objects.create(
                        tenant=self.tenant, request=req, item=item, quantity=qty, unit_price=price,
                        created_by=self.user,
                    )
                    subtotal += qty * price
                req.subtotal = subtotal
                req.total_amount = subtotal
                req.save(update_fields=['subtotal', 'total_amount'])
                if self.scale > 1:
                    dt = timezone.make_aware(datetime.combine(self._spread_day(i, len(statuses)), dtime(hour=self.rng.randint(8, 18))))
                    AgentInvoiceRequest.objects.filter(pk=req.pk).update(created_at=dt)
                return req

            if self._safe(f'طلب فاتورة مندوب #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ طلبات فواتير مناديب: {created}')

    # ── مخازن ─────────────────────────────────────────────────────────────
    def _create_stock_transfers(self):
        from apps.stocks.models import StockTransfer, StockTransferLine, StockQuantity
        from apps.stocks.services import confirm_stock_transfer

        if not self.second_stock:
            self.stdout.write('  ⚠ لا يوجد مخزن ثانٍ — تخطي تحويلات المخزون')
            return

        created = 0
        n = self._per_month(5, 4)
        for i in range(n):
            t_date = self._spread_day(i, n)
            item = self.items[i % len(self.items)]

            def _build(item=item, t_date=t_date):
                self._ensure_stock(item, Decimal('20'), t_date)
                sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.main_stock, item=item)
                available = sq.quantity - sq.reserved_quantity
                qty = min(Decimal('20'), available)
                if qty <= 0:
                    raise ValueError('لا يوجد رصيد كافٍ للتحويل')
                tr = StockTransfer.objects.create(
                    tenant=self.tenant, transfer_date=t_date,
                    from_stock=self.main_stock, to_stock=self.second_stock,
                    notes='تغذية مخزون الفرع الثانوي', created_by=self.user,
                )
                StockTransferLine.objects.create(tenant=self.tenant, transfer=tr, item=item, quantity=qty, created_by=self.user)
                confirm_stock_transfer(tr)
                return tr

            if self._safe(f'تحويل مخزون #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ تحويلات مخزون: {created}')

    def _create_stocktakes(self):
        from apps.stocks.models import Stocktake, StocktakeLine, StockQuantity
        from apps.stocks.services import confirm_stocktake

        created = 0
        n = self._n(4)
        for i in range(n):
            s_date = self._spread_day(i, n)
            sample_items = self.rng.sample(self.items, k=min(5, len(self.items)))

            def _build(sample_items=sample_items, s_date=s_date):
                st = Stocktake.objects.create(
                    tenant=self.tenant, stocktake_date=s_date, stock=self.main_stock,
                    status='draft', notes='جرد دوري' if self.scale > 1 else 'جرد دوري أسبوعي', created_by=self.user,
                )
                for item in sample_items:
                    sq = StockQuantity.objects.filter(tenant=self.tenant, stock=self.main_stock, item=item).first()
                    system_qty = sq.quantity if sq else Decimal('0')
                    variance = Decimal(str(self.rng.choice([-2, -1, 0, 0, 1, 2])))
                    counted = max(Decimal('0'), system_qty + variance)
                    StocktakeLine.objects.create(
                        tenant=self.tenant, stocktake=st, item=item,
                        system_quantity=system_qty, counted_quantity=counted, created_by=self.user,
                    )
                confirm_stocktake(st)
                return st

            if self._safe(f'جرد #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ عمليات جرد: {created}')

    def _create_stock_destructions(self):
        from apps.stocks.models import StockDestruction, StockDestructionLine, StockQuantity
        from apps.stocks.services import confirm_stock_destruction

        created = 0
        reasons = ['expired', 'damaged', 'expired', 'other']
        reasons = (reasons * self._n(1))[:self._per_month(len(reasons), 3)]
        for i, reason in enumerate(reasons):
            d_date = self._spread_day(i, len(reasons))
            item = self.items[(i * 3) % len(self.items)]

            def _build(item=item, d_date=d_date, reason=reason, i=i):
                self._ensure_stock(item, Decimal('3'), d_date)
                sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.main_stock, item=item)
                qty = min(Decimal('3'), sq.quantity)
                if qty <= 0:
                    raise ValueError('لا يوجد رصيد للإتلاف')
                dst = StockDestruction.objects.create(
                    tenant=self.tenant, destruction_date=d_date, stock=self.main_stock,
                    status='draft', reason=reason, witness_name='طارق النور محجوب — مسؤول مشتريات',
                    reference_number=f'DES-WIT-{i+1:03d}', created_by=self.user,
                )
                StockDestructionLine.objects.create(
                    tenant=self.tenant, destruction=dst, item=item, quantity=qty,
                    unit_cost_snapshot=item.cost_price, created_by=self.user,
                )
                confirm_stock_destruction(dst, self.user)
                return dst

            if self._safe(f'إتلاف #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ سجلات إتلاف: {created}')

    # ── متجر إلكتروني ─────────────────────────────────────────────────────
    def _create_store_orders(self):
        from apps.store.models import StoreSettings, OnlineOrder, OnlineOrderLine
        from apps.store.services import approve_order, reject_order
        from apps.sales.services import confirm_sale_invoice

        store = StoreSettings.objects.filter(tenant=self.tenant).first()
        if not store:
            self.stdout.write('  ⚠ لا توجد إعدادات متجر إلكتروني — تخطي')
            return

        statuses = ['pending', 'approved', 'pending', 'rejected', 'approved', 'pending', 'approved', 'rejected', 'pending', 'approved']
        statuses = (statuses * self._n(1))[:self._per_month(len(statuses), 12)]
        created = 0
        for i, status in enumerate(statuses):
            item = self.items[i % len(self.items)]
            qty = Decimal(str(self.rng.randint(1, 5)))

            def _build(item=item, qty=qty, status=status, i=i):
                order = OnlineOrder.objects.create(
                    tenant=self.tenant, store=store,
                    customer_name=f'زبون أونلاين {i+1}',
                    customer_phone=f'0912{i:06d}',
                    payment_method='bank', status='pending',
                    subtotal=item.selling_price * qty,
                    total_amount=item.selling_price * qty,
                )
                OnlineOrderLine.objects.create(
                    tenant=self.tenant, order=order, item=item,
                    item_name=item.name, unit_price=item.selling_price, quantity=qty,
                )
                backdated_date = self._spread_day(i, len(statuses))
                backdated = timezone.make_aware(datetime.combine(backdated_date, dtime(hour=self.rng.randint(9, 20))))
                OnlineOrder.objects.filter(pk=order.pk).update(created_at=backdated)

                if status == 'approved':
                    self._ensure_stock(item, qty, backdated_date)
                    invoice = approve_order(order)
                    invoice.invoice_date = backdated_date
                    invoice.payment_method = 'cash'
                    invoice.save(update_fields=['invoice_date', 'payment_method'])
                    confirm_sale_invoice(invoice, self.user)
                    order.refresh_from_db()
                elif status == 'rejected':
                    reject_order(order)
                return order

            if self._safe(f'طلب متجر #{i+1}', _build) is not None:
                created += 1
        self.stdout.write(f'  ✓ طلبات متجر إلكتروني: {created}')

    # ── إشعارات ───────────────────────────────────────────────────────────
    def _create_notifications(self):
        from apps.notifications.models import Notification
        low_item = min(self.items, key=lambda i: i.selling_price).name if self.items else 'صنف'
        credit_customer = next((c for c in self.customers if (c.credit_limit or 0) > 0), None)

        data = [
            ('low_stock', 'high', 'تنبيه مخزون منخفض', f'الصنف «{low_item}» يقترب من الحد الأدنى للمخزون'),
            ('online_order', 'high', 'طلب جديد من المتجر', 'لديك طلبات جديدة بانتظار المراجعة في المتجر الإلكتروني'),
            ('general', 'low', 'ملخص أسبوعي', 'تم تسجيل حركة نشطة هذا الأسبوع عبر المبيعات والمشتريات'),
            ('expiry_soon', 'medium', 'قرب انتهاء صلاحية', 'توجد دفعات أدوية تقترب من تاريخ انتهاء الصلاحية'),
            ('rfq_expiry', 'low', 'انتهاء صلاحية طلب سعر', 'أحد طلبات عروض الأسعار على وشك الانتهاء'),
            ('transfer_done', 'low', 'اكتمال تحويل مخزون', 'تم تأكيد تحويل مخزون بين الفروع بنجاح'),
            ('stocktake_done', 'medium', 'اكتمال جرد مخزون', 'تم تأكيد جلسة جرد للمخزن الرئيسي'),
            ('agent_request', 'medium', 'طلب مندوب جديد', 'أحد المناديب أرسل طلب فاتورة جديد بانتظار المراجعة'),
        ]
        if credit_customer:
            data.append((
                'overdue_invoice', 'medium', 'متابعة رصيد عميل',
                f'العميل «{credit_customer.name}» لديه رصيد آجل يستحق المتابعة',
            ))
        data.append(('general', 'medium', 'تسوية تأمين معلّقة', 'توجد مطالبة تأمين بانتظار التسوية النهائية'))
        data.append(('general', 'low', 'تذكرة دعم مفتوحة', 'توجد تذكرة دعم فني بانتظار رد فريق المنصة'))

        created = 0
        data = (data * self._n(1))[:self._n(len(data))]
        for i, (ntype, priority, title, msg) in enumerate(data):
            n = Notification.objects.create(
                tenant=self.tenant, user=self.user, notification_type=ntype,
                priority=priority, title=title, message=msg, is_read=self.rng.choice([True, False]),
            )
            if self.scale > 1:
                dt = timezone.make_aware(datetime.combine(self._spread_day(i, len(data)), dtime(hour=self.rng.randint(8, 20))))
                Notification.objects.filter(pk=n.pk).update(created_at=dt)
                # الإشعارات الأقدم من أسبوعين تُعتبر مقروءة
                if (self.today - dt.date()).days > 14:
                    Notification.objects.filter(pk=n.pk).update(is_read=True)
            created += 1
        self.stdout.write(f'  ✓ إشعارات: {created}')

    # ── دعم فني ───────────────────────────────────────────────────────────
    def _create_support_tickets(self):
        from apps.core.models import SupportTicket, SupportMessage
        tickets_data = [
            ('استفسار عن تفعيل بوابة العملاء', 'account', 'medium', 'open',
             'نرغب في تفعيل بوابة العملاء الإلكترونية لعملائنا — ما الخطوات المطلوبة؟'),
            ('مشكلة في طباعة فاتورة المبيعات', 'technical', 'high', 'in_progress',
             'عند طباعة بعض فواتير المبيعات لا يظهر اسم المندوب رغم تفعيل الخيار.'),
            ('طلب زيادة عدد المخازن المتاحة', 'billing', 'low', 'resolved',
             'هل يمكن ترقية الباقة لإضافة مخازن فرعية إضافية؟'),
            ('استفسار عن تقرير حركة المخزون', 'feature', 'medium', 'closed',
             'نحتاج تقريراً يوضح حركة كل صنف بين المخازن خلال فترة محددة.'),
        ]
        created = 0
        tickets_data = (tickets_data * max(1, round(self.scale / 4)))[:max(4, round(self.scale))]
        for i, (subject, category, priority, status, desc) in enumerate(tickets_data):
            ticket = SupportTicket.objects.create(
                tenant=self.tenant, created_by=self.user, subject=subject,
                description=desc, category=category, priority=priority, status=status,
            )
            SupportMessage.objects.create(
                ticket=ticket, sender=self.user, sender_type='tenant', body=desc,
            )
            if status in ('in_progress', 'resolved', 'closed'):
                SupportMessage.objects.create(
                    ticket=ticket, sender=None, sender_type='admin',
                    body='تم استلام طلبكم وجاري العمل عليه من فريق الدعم الفني.',
                )
            if self.scale > 1:
                dt = timezone.make_aware(datetime.combine(self._spread_day(i, len(tickets_data)), dtime(hour=self.rng.randint(8, 18))))
                SupportTicket.objects.filter(pk=ticket.pk).update(created_at=dt)
                SupportMessage.objects.filter(ticket=ticket).update(created_at=dt)
            created += 1
        self.stdout.write(f'  ✓ تذاكر دعم فني: {created}')

    # ── سعر الصرف ────────────────────────────────────────────────────────
    def _create_exchange_rate_history(self):
        from apps.core.models import ExchangeRateHistory
        base_rate = self.tenant.exchange_rate or Decimal('7500')
        created = 0
        deltas = [-50, -20, 0]
        if self.scale > 1:
            # تحديث نصف شهري تدريجي صعوداً حتى السعر الحالي
            steps = max(3, self.months * 2)
            deltas = [-int(base_rate * Decimal('0.02')) * (steps - 1 - k) // max(steps - 1, 1) for k in range(steps)]
        for i, delta in enumerate(deltas):
            h = ExchangeRateHistory.objects.create(
                tenant=self.tenant, rate=base_rate + delta, changed_by=self.user,
                notes='تحديث دوري لسعر الصرف',
            )
            changed_dt = timezone.make_aware(datetime.combine(self._spread_day(len(deltas) - 1 - i, len(deltas)), dtime(hour=10)))
            ExchangeRateHistory.objects.filter(pk=h.pk).update(changed_at=changed_dt)
            created += 1
        self.stdout.write(f'  ✓ سجل أسعار الصرف: {created}')

    def _create_activity_log(self):
        from apps.core.models import ActivityLog
        entries = [
            ('create', 'sales.SaleInvoice', 'إنشاء فاتورة بيع جديدة'),
            ('create', 'purchases.PurchaseInvoice', 'إنشاء أمر شراء جديد'),
            ('update', 'items.Item', 'تحديث بيانات صنف'),
            ('create', 'expenses.Expense', 'تسجيل مصروف جديد'),
            ('confirm', 'sales.SaleInvoice', 'تأكيد فاتورة بيع'),
            ('create', 'employees.EmployeeAdvance', 'صرف سلفة لموظف'),
            ('create', 'stocks.StockTransfer', 'تحويل مخزون بين فرعين'),
            ('create', 'insurance.InsuranceClaim', 'تقديم مطالبة تأمين'),
            ('login', '', 'تسجيل دخول للنظام'),
            ('view', 'sales.reports', 'عرض تقرير المبيعات الأسبوعي'),
        ]
        created = 0
        entries = (entries * self._n(1))[:self._n(len(entries))]
        for i, (action, model_name, desc) in enumerate(entries):
            log = ActivityLog.objects.create(
                tenant=self.tenant, user=self.user, action=action,
                model_name=model_name, description=desc,
            )
            created_dt = timezone.make_aware(datetime.combine(self._spread_day(i, len(entries)), dtime(hour=self.rng.randint(8, 19))))
            ActivityLog.objects.filter(pk=log.pk).update(created_at=created_dt)
            created += 1
        self.stdout.write(f'  ✓ سجل نشاطات: {created}')

    def _backdate_created_at(self):
        """مؤكِّدات الخدمات تكتب created_at بلحظة التشغيل — نُرجعها ليوم المستند
        كي تعمل اللوحات والتقارير المبنية على created_at بشكل صحيح."""
        from apps.sales.models import SaleInvoice, SaleReturn
        from apps.purchases.models import PurchaseInvoice, PurchaseReturn
        from apps.expenses.models import Expense
        plan = [
            (SaleInvoice, 'invoice_date'), (SaleReturn, 'return_date'),
            (PurchaseInvoice, 'invoice_date'), (PurchaseReturn, 'return_date'),
            (Expense, 'expense_date'),
        ]
        total = 0
        for model, field in plan:
            for pk, d in model.objects.filter(tenant=self.tenant).values_list('pk', field):
                if not d:
                    continue
                dt = timezone.make_aware(datetime.combine(d, dtime(hour=self.rng.randint(8, 19), minute=self.rng.randint(0, 59))))
                model.objects.filter(pk=pk).update(created_at=dt)
                total += 1
        self.stdout.write(f'  ✓ إعادة تأريخ created_at لـ {total} مستند')

    def _coverage_report(self):
        from django.apps import apps as django_apps
        empty, filled = [], []
        for m in django_apps.get_models():
            if m._meta.app_label in ('admin', 'auth', 'contenttypes', 'sessions'):
                continue
            if not any(f.name == 'tenant' for f in m._meta.fields):
                continue
            n = m.objects.filter(tenant=self.tenant).count()
            (filled if n else empty).append(f'{m._meta.label}={n}' if n else m._meta.label)
        self.stdout.write('')
        self.stdout.write(f'  📦 جداول فيها بيانات ({len(filled)}): ' + ', '.join(filled))
        self.stdout.write(self.style.WARNING(f'  ∅ جداول فارغة ({len(empty)}): ' + ', '.join(empty)))

    # ── ملخص ختامي ───────────────────────────────────────────────────────
    def _totals(self):
        from apps.sales.models import SaleInvoice, SaleInvoiceLine
        from apps.expenses.models import Expense
        from apps.purchases.models import PurchaseInvoice
        from apps.employees.models import EmployeeSalaryPayment
        from django.db.models import Sum, F
        live = ['confirmed', 'partially_returned']
        sales = SaleInvoice.objects.filter(tenant=self.tenant, status__in=live).aggregate(s=Sum('grand_total'))['s'] or Decimal('0')
        cogs = SaleInvoiceLine.objects.filter(
            tenant=self.tenant, invoice__status__in=live,
        ).aggregate(s=Sum(F('quantity') * F('cost_price_snapshot')))['s'] or Decimal('0')
        purchases = PurchaseInvoice.objects.filter(tenant=self.tenant, status__in=live).aggregate(s=Sum('grand_total'))['s'] or Decimal('0')
        expenses = Expense.objects.filter(tenant=self.tenant, status='confirmed').aggregate(s=Sum('amount'))['s'] or Decimal('0')
        salaries = EmployeeSalaryPayment.objects.filter(tenant=self.tenant, status='paid').aggregate(s=Sum('base_salary'))['s'] or Decimal('0')
        return dict(sales=sales, cogs=cogs, purchases=purchases, expenses=expenses, salaries=salaries)

    def _print_summary(self):
        cur = self.tenant.currency
        t = self._totals()
        b = self._baseline
        d = {k: t[k] - b[k] for k in t}
        self.stdout.write('')
        self.stdout.write(f'  📊 هذه العملية ({self.days} يوم):')
        self.stdout.write(f'     المبيعات           {d["sales"]:>20,.0f} {cur}')
        self.stdout.write(f'     تكلفة البضاعة      {d["cogs"]:>20,.0f} {cur}')
        gross = d['sales'] - d['cogs']
        margin = (gross / d['sales'] * 100) if d['sales'] else Decimal('0')
        self.stdout.write(f'     مجمل الربح         {gross:>20,.0f} {cur}  ({margin:.1f}%)')
        self.stdout.write(f'     المصروفات          {d["expenses"]:>20,.0f} {cur}')
        self.stdout.write(f'     الرواتب            {d["salaries"]:>20,.0f} {cur}')
        net = gross - d['expenses'] - d['salaries']
        self.stdout.write(f'     صافي الربح التقديري {net:>19,.0f} {cur}')
        self.stdout.write(f'     المشتريات          {d["purchases"]:>20,.0f} {cur}  (تشمل مخزوناً متبقياً)')
        if d['sales'] > d['expenses'] + d['salaries']:
            self.stdout.write(self.style.SUCCESS('  ✅ المبيعات أعلى من المصروفات والرواتب بفارق كبير'))
        else:
            self.stdout.write(self.style.ERROR('  ❌ تحذير: المصروفات أعلى من أو تساوي المبيعات!'))
