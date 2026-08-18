"""
AI Services — DeepSeek integration for EnjazIMS
================================================
Collects real business data per tenant, then queries DeepSeek to generate
Arabic business insights, chat responses, and smart notification analysis.
"""

import json
import logging
import requests
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, Count, Q, F

from apps.core.constants import CURRENCY_AR

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# DeepSeek API call
# ──────────────────────────────────────────────────────────────

def _call_deepseek(messages: list, max_tokens: int = 600) -> str:
    """Send messages to DeepSeek and return the assistant reply."""
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        return "مفتاح API غير مُعيَّن. يرجى إضافة DEEPSEEK_API_KEY في secrets.json."

    try:
        response = requests.post(
            settings.DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.DEEPSEEK_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except requests.Timeout:
        logger.warning("DeepSeek API timeout")
        return "انتهت مهلة الاتصال بالمساعد الذكي. يرجى المحاولة مرة أخرى."
    except requests.RequestException as exc:
        logger.error("DeepSeek API error: %s", exc)
        return "تعذّر الاتصال بالمساعد الذكي في الوقت الحالي."
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("DeepSeek response parse error: %s", exc)
        return "حدث خطأ أثناء معالجة رد المساعد الذكي."


# ──────────────────────────────────────────────────────────────
# Business context builder
# ──────────────────────────────────────────────────────────────

def _decimal_to_float(value):
    """Convert Decimal to float for JSON serialization."""
    return float(value) if isinstance(value, Decimal) else value


def collect_business_context(tenant) -> dict:
    """
    Gather key business metrics for the last 30 days for the given tenant.
    Returns a serializable dict suitable for embedding in the AI prompt.
    """
    from apps.sales.models import SaleInvoice, SaleInvoiceLine
    from apps.items.models import Item
    from apps.stocks.models import StockQuantity
    from apps.customers.models import Customer
    from apps.purchases.models import PurchaseInvoice
    from apps.expenses.models import Expense

    now = timezone.localdate()
    month_ago = now - timedelta(days=30)
    week_ago = now - timedelta(days=7)

    # ── Sales ────────────────────────────────────────────────
    confirmed_sales = SaleInvoice.objects.filter(
        tenant=tenant, status='confirmed'
    )
    monthly_sales = confirmed_sales.filter(invoice_date__gte=month_ago)
    weekly_sales  = confirmed_sales.filter(invoice_date__gte=week_ago)

    monthly_revenue = _decimal_to_float(
        monthly_sales.aggregate(t=Sum('grand_total'))['t'] or 0
    )
    weekly_revenue = _decimal_to_float(
        weekly_sales.aggregate(t=Sum('grand_total'))['t'] or 0
    )
    monthly_invoice_count = monthly_sales.count()

    # ── Top selling items (by revenue) ───────────────────────
    top_items_qs = (
        SaleInvoiceLine.objects
        .filter(invoice__tenant=tenant, invoice__status='confirmed',
                invoice__invoice_date__gte=month_ago)
        .values('item__name')
        .annotate(total_qty=Sum('quantity'), total_rev=Sum('line_total'))
        .order_by('-total_rev')[:5]
    )
    top_items = [
        {
            'name': r['item__name'],
            'qty': _decimal_to_float(r['total_qty']),
            'revenue': _decimal_to_float(r['total_rev']),
        }
        for r in top_items_qs
    ]

    # ── Low stock items ───────────────────────────────────────
    low_stock_qs = (
        StockQuantity.objects
        .filter(tenant=tenant, item__is_active=True, item__min_quantity__gt=0)
        .filter(quantity__lte=F('item__min_quantity'))
        .select_related('item', 'stock')
        .order_by('quantity')[:10]
    )
    low_stock = [
        {
            'item': sq.item.name,
            'current': _decimal_to_float(sq.quantity),
            'min': _decimal_to_float(sq.item.min_quantity),
            'stock': sq.stock.name,
        }
        for sq in low_stock_qs
    ]

    # ── Top customer debtors ─────────────────────────────────
    from apps.sales.models import CustomerLedger
    debtor_qs = (
        CustomerLedger.objects
        .filter(tenant=tenant)
        .values('customer__name')
        .annotate(balance=Sum('amount'))
        .filter(balance__gt=0)
        .order_by('-balance')[:5]
    )
    top_debtors = [
        {'name': r['customer__name'], 'balance': _decimal_to_float(r['balance'])}
        for r in debtor_qs
    ]

    # ── Monthly expenses ─────────────────────────────────────
    monthly_expenses = _decimal_to_float(
        Expense.objects
        .filter(tenant=tenant, expense_date__gte=month_ago)
        .aggregate(t=Sum('amount'))['t'] or 0
    )

    # ── Recent purchases ─────────────────────────────────────
    recent_purchases = (
        PurchaseInvoice.objects
        .filter(tenant=tenant, status='confirmed', invoice_date__gte=month_ago)
        .aggregate(t=Sum('grand_total'))['t'] or 0
    )
    monthly_purchases = _decimal_to_float(recent_purchases)

    # ── Employee payroll (last 30 days) ───────────────────────
    employee_data = {}
    try:
        from apps.employees.models import Employee, SalaryPayment, EmployeeAdvance
        employee_count = Employee.objects.filter(tenant=tenant, is_active=True).count()
        monthly_salaries = _decimal_to_float(
            SalaryPayment.objects
            .filter(tenant=tenant, status='paid', period_start__gte=month_ago)
            .aggregate(t=Sum('net_salary'))['t'] or 0
        )
        pending_advances = _decimal_to_float(
            EmployeeAdvance.objects
            .filter(tenant=tenant, status='active')
            .aggregate(t=Sum('amount'))['t'] or 0
        )
        employee_data = {
            'active_employees': employee_count,
            'monthly_salaries': monthly_salaries,
            'pending_advances': pending_advances,
        }
    except Exception:
        pass

    # ── Hard currency info ────────────────────────────────────
    hc_info = {}
    if getattr(tenant, 'hard_currency_mode', False):
        try:
            from apps.treasury.models import Treasury
            hc_treasury = Treasury.objects.filter(tenant=tenant, is_hard_currency=True).first()
            hc_info = {
                'enabled': True,
                'currency': tenant.hard_currency or 'USD',
                'exchange_rate': float(tenant.exchange_rate or 1),
                'hc_balance': float(hc_treasury.current_balance) if hc_treasury else 0,
            }
        except Exception:
            pass

    return {
        'period': f"{month_ago} → {now}",
        'currency': tenant.currency or 'SDG',
        'monthly_revenue': monthly_revenue,
        'weekly_revenue': weekly_revenue,
        'monthly_invoice_count': monthly_invoice_count,
        'monthly_purchases': monthly_purchases,
        'monthly_expenses': monthly_expenses,
        'gross_profit': round(monthly_revenue - monthly_purchases, 2),
        'top_selling_items': top_items,
        'low_stock_items': low_stock,
        'top_debtors': top_debtors,
        'employee_data': employee_data,
        'hc_info': hc_info,
    }


# ──────────────────────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """أنت مساعد أعمال ذكي متخصص في تحليل بيانات المخزون والمبيعات.
تعمل داخل نظام إدارة مخزون (EnjazIMS) لصاحب المحل.
قواعد الرد الصارمة:
- أجب دائماً بالعربية، بأسلوب مهني وموجز.
- استخدم دائماً رمز العملة العربي الموجود في البيانات ولا تُبدّله بعملة أخرى.
- إذا كانت العملة معروفة برمز عربي مثل ج.س أو د.إ فاذكرها بدلاً من رمز العملة الإنجليزي.
- اكتب النص بدون أي تنسيق Markdown: لا نجوم (**) ولا شرطات سفلية ولا علامات # للعناوين.
- استخدم الأرقام والنقاط والعناوين النصية العادية فقط.
- استند إلى الأرقام المُقدَّمة واستنتج منها بشكل منطقي.
- قدّم توصيات عملية قابلة للتنفيذ.
- لا تتجاوز 300 كلمة ما لم يطلب المستخدم تفصيلاً أكثر.
- لا تخترع أرقاماً أو معلومات غير موجودة في السياق."""


def _build_context_message(context: dict) -> str:
    """Format business context as a readable Arabic text block."""
    ctx = context
    cur = str(ctx.get('currency', '')).strip().upper()
    cur_label = CURRENCY_AR.get(cur, cur)
    lines = [
        f"📊 بيانات الأعمال ({ctx['period']}) — العملة: {cur_label}",
        f"  • إيرادات الشهر: {ctx['monthly_revenue']:,.0f} {cur_label}",
        f"  • إيرادات الأسبوع: {ctx['weekly_revenue']:,.0f} {cur_label}",
        f"  • عدد الفواتير: {ctx['monthly_invoice_count']}",
        f"  • مشتريات الشهر: {ctx['monthly_purchases']:,.0f} {cur_label}",
        f"  • مصروفات الشهر: {ctx['monthly_expenses']:,.0f} {cur_label}",
        f"  • إجمالي الربح: {ctx['gross_profit']:,.0f} {cur_label}",
    ]

    if ctx['top_selling_items']:
        lines.append("\n🏆 أكثر المنتجات مبيعاً:")
        for i in ctx['top_selling_items']:
            lines.append(f"  • {i['name']}: {i['qty']:.0f} وحدة / {i['revenue']:,.0f} {cur_label}")

    if ctx['low_stock_items']:
        lines.append("\n⚠️ منتجات تحت الحد الأدنى:")
        for i in ctx['low_stock_items']:
            lines.append(f"  • {i['item']}: {i['current']:.0f} متبقي (الحد: {i['min']:.0f}) — {i['stock']}")

    if ctx['top_debtors']:
        lines.append("\n💳 أعلى أرصدة العملاء:")
        for d in ctx['top_debtors']:
            lines.append(f"  • {d['name']}: {d['balance']:,.0f} {cur_label}")

    emp = ctx.get('employee_data', {})
    if emp:
        lines.append(f"\n👥 الموظفون: {emp.get('active_employees', 0)} موظف نشط")
        if emp.get('monthly_salaries'):
            lines.append(f"  • رواتب مدفوعة هذا الشهر: {emp['monthly_salaries']:,.0f} {cur_label}")
        if emp.get('pending_advances'):
            lines.append(f"  • سلف معلقة: {emp['pending_advances']:,.0f} {cur_label}")

    hc = ctx.get('hc_info', {})
    if hc.get('enabled'):
        hc_cur = str(hc.get('currency', '')).strip().upper()
        hc_label = CURRENCY_AR.get(hc_cur, hc_cur)
        lines.append(f"\n💱 وضع العملة الصعبة: مفعّل — {hc_label}")
        lines.append(f"  • سعر الصرف الحالي: 1 {hc_label} = {hc.get('exchange_rate', 1):,.2f} {cur_label}")
        lines.append(f"  • رصيد خزينة {hc_label}: {hc.get('hc_balance', 0):,.2f} {hc_label}")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Public API — Chat
# ──────────────────────────────────────────────────────────────

def chat(user_message: str, history: list, tenant) -> str:
    """
    Handle a user chat message.
    history: list of {"role": "user"|"assistant", "content": str}
    Returns the assistant reply string.
    """
    context = collect_business_context(tenant)
    context_text = _build_context_message(context)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": context_text},
        {"role": "assistant", "content": "حسناً، لديّ البيانات. كيف يمكنني مساعدتك؟"},
    ]

    # Append trimmed history (last 6 turns to stay within token budget)
    for turn in history[-6:]:
        messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": user_message})

    return _call_deepseek(messages, max_tokens=600)


# ──────────────────────────────────────────────────────────────
# Public API — Daily Insights
# ──────────────────────────────────────────────────────────────

def generate_daily_insights(tenant) -> str:
    """
    Generate a concise Arabic business health summary for the dashboard widget.
    """
    context = collect_business_context(tenant)
    context_text = _build_context_message(context)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{context_text}\n\n"
                "بناءً على هذه البيانات، اكتب تقرير صحة أعمال يومي موجز (5 نقاط كحد أقصى) "
                "يشمل: أبرز إنجاز، أبرز تحذير، وتوصية واحدة عملية فورية."
            ),
        },
    ]

    return _call_deepseek(messages, max_tokens=400)


# ──────────────────────────────────────────────────────────────
# Public API — Smart Notification Analysis
# ──────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────
# Public API — Smart Import Header Mapping
# ──────────────────────────────────────────────────────────────

def match_category_name(written_name: str, existing_names: list) -> str | None:
    """
    Use AI to find the closest existing category name for a written name that
    didn't match exactly.

    Returns the matched name from existing_names, or None (caller should create new).
    Falls back to None if AI is unavailable.
    """
    if not existing_names:
        return None

    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        return None

    names_text = "، ".join(f'"{n}"' for n in existing_names[:60])

    prompt = (
        f'اسم التصنيف في الملف: "{written_name}"\n'
        f'التصنيفات الموجودة في النظام: {names_text}\n\n'
        'هل يطابق اسم الملف أياً من التصنيفات الموجودة (مع مراعاة الأخطاء الإملائية والاختصارات والاختلافات البسيطة)؟\n'
        'إذا نعم: اكتب الاسم الدقيق من القائمة فقط بدون أي نص آخر.\n'
        'إذا لا: اكتب كلمة "جديد" فقط.'
    )

    messages = [
        {"role": "system", "content": "أنت نظام مطابقة أسماء دقيق. أجب بالاسم الدقيق من القائمة أو بكلمة 'جديد' فقط، بدون أي نص إضافي."},
        {"role": "user", "content": prompt},
    ]

    try:
        result = _call_deepseek(messages, max_tokens=60).strip().strip('"').strip("'")
    except Exception:
        return None

    if not result or result == 'جديد':
        return None

    if result in existing_names:
        return result

    result_lower = result.lower()
    for name in existing_names:
        if name.lower() == result_lower:
            return name

    return None


def smart_map_headers(actual_headers: list, field_schema: list) -> dict:
    """
    Use AI to semantically map actual file column headers to expected field names.

    field_schema: list of {"field": str, "description": str, "required": bool}
    Returns: {actual_header: canonical_field_name}
    Falls back to empty dict if AI is unavailable or response is unparseable.
    """
    if not actual_headers:
        return {}

    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        return {}

    schema_lines = "\n".join(
        f'- {f["field"]}: {f["description"]}{"  (مطلوب)" if f.get("required") else ""}'
        for f in field_schema
    )
    headers_text = "، ".join(f'"{h}"' for h in actual_headers if h)

    prompt = (
        f"أعمدة الملف المرفوع: {headers_text}\n\n"
        f"الحقول المتوقعة:\n{schema_lines}\n\n"
        "عيّن كل عمود إلى الحقل الأنسب دلالياً (المعنى وليس التطابق الحرفي).\n"
        "أجب بـ JSON فقط بلا أي نص إضافي، بالشكل:\n"
        '{"اسم العمود في الملف": "اسم_الحقل", ...}\n'
        "إذا لم يتطابق عمود مع أي حقل، لا تُدرجه."
    )

    messages = [
        {"role": "system", "content": "أنت نظام تعيين أعمدة بيانات. أجب بـ JSON صحيح فقط بلا مقدمة أو شرح."},
        {"role": "user", "content": prompt},
    ]

    raw = _call_deepseek(messages, max_tokens=400)

    try:
        json_match = __import__('re').search(r'\{[^{}]*\}', raw, __import__('re').DOTALL)
        if json_match:
            mapping = json.loads(json_match.group())
            valid_fields = {f["field"] for f in field_schema}
            header_set = set(actual_headers)
            return {
                k: v
                for k, v in mapping.items()
                if isinstance(k, str) and isinstance(v, str)
                and v in valid_fields
                and k in header_set
            }
    except (json.JSONDecodeError, AttributeError, TypeError):
        logger.warning("smart_map_headers: failed to parse AI response: %s", raw[:200])

    return {}


def enrich_notification(notification_type: str, raw_message: str, tenant) -> str:
    """
    Given a raw notification message (e.g., "المخزون منخفض لمنتج X"),
    return an AI-enriched version with context and actionable advice.
    """
    context = collect_business_context(tenant)
    context_text = _build_context_message(context)

    prompt = (
        f"الإشعار الأصلي: {raw_message}\n\n"
        f"{context_text}\n\n"
        "اكتب رسالة إشعار محسّنة (3 أسطر كحد أقصى) تتضمن: "
        "توصيفاً دقيقاً للمشكلة، وتوصية فورية واحدة."
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    return _call_deepseek(messages, max_tokens=200)


# ──────────────────────────────────────────────────────────────
# Public API — Marketing Social Media Post Generation
# ──────────────────────────────────────────────────────────────

_MARKETING_FEATURES_REFERENCE = (
    "حقائق عن نظام إنجاز (EnjazIMS) يجب الاستناد إليها فقط، دون اختراع أي ميزة أو رقم غير مذكور هنا. "
    "استخدم اسم \"إنجاز\" في نص المنشور، لا \"EnjazIMS\":\n"
    "- نظام سحابي لإدارة المخزون والمبيعات، بواجهة عربية كاملة من الأساس، وليست مترجمة.\n"
    "- يدعم محلاً واحداً بمخزن واحد، أو مخازن متعددة، أو فروعاً متعددة (حتى عشرين فرعاً)، بنفس السهولة.\n"
    "- نسخة احتياطية يومية تلقائية لبيانات كل مشترك، مع عزل تام لبياناته عن أي مشترك آخر.\n"
    "- نقطة بيع سريعة (POS)، فواتير، عروض أسعار، مرتجعات، وتسليم مؤجل.\n"
    "- إدارة مشتريات كاملة: طلبات عروض أسعار، أوامر شراء، مرتجعات مشتريات.\n"
    "- إدارة مخازن: أرصدة افتتاحية، تحويلات بين المخازن، جرد مخزون، تتبع تواريخ الصلاحية والدُفعات للمنتجات الغذائية والدوائية.\n"
    "- متجر إلكتروني عام مجاني مع كل باقة، بسلة شراء وصفحة مستقلة لكل مشترك.\n"
    "- بوابة عميل: رابط خاص لكل عميل يعرض فواتيره وكشف حسابه.\n"
    "- ذكاء اصطناعي مدمج: دردشة ذكية ورؤى يومية تلقائية عن أداء العمل.\n"
    "- إشعارات ذكية تلقائية (مخزون منخفض، فاتورة متأخرة، طلب جديد).\n"
    "- أكثر من 27 تقريراً: مبيعات، مشتريات، مخزون، مصروفات، خزائن، قائمة الدخل.\n"
    "- إدارة مصروفات وخزائن (نقدية وبنكية) مع تحويلات بينها.\n"
    "- نظام صلاحيات دقيق: 23 قسماً و151 صلاحية، وسجل نشاط كامل لكل عملية.\n"
    "- دعم فني عبر نظام تذاكر مدمج.\n"
    "- تجربة مجانية لمدة 7 أيام، بدون التزام."
)

_MARKETING_CATEGORY_GUIDANCE = {
    'comprehensive': 'منشور شامل يلخّص أهم ميزات النظام في فقرة واحدة متماسكة، دون أن يتحول لقائمة.',
    'problem': 'يبدأ من مشكلة يومية حقيقية يعيشها صاحب محل (دفاتر ورقية، جرد يدوي، حسابات خاطئة...)، ثم يشير لكيف يحل النظام هذه المشكلة تحديداً.',
    'feature': 'يشرح ميزة واحدة فقط من ميزات النظام بعمق، لا أكثر من ميزة في نفس المنشور.',
    'business_type': 'موجّه لأحد الأنشطة التالية تحديداً: تجارة عامة (قطع غيار، إلكترونيات)، أو أغذية وصيدليات (تتبع صلاحية)، أو مطاعم ومقاهي (وصفات ومواد خام). اختر نشاطاً واحداً واجعل المنشور خاصاً به.',
    'trust': 'يعالج هاجس الأمان أو الثقة في نظام سحابي: حماية البيانات، النسخ الاحتياطي، العزل بين المشتركين، الصلاحيات.',
    'objection': 'يطرح اعتراضاً واقعياً يقوله تاجر مثل "معقد وما عندي وقت أتعلمه" أو "عندي نظام قديم شغال"، ثم يرد عليه بإجابة مقنعة وصادقة.',
    'tip': 'نصيحة تجارية عملية مفيدة بذاتها لصاحب أي محل، مع ربط خفيف بكيف يسهّل النظام تطبيقها.',
    'comparison': 'يقارن بين طريقة عمل تقليدية (دفتر ورقي، إكسل، برنامج قديم، الذاكرة) وبين نظام إدارة حقيقي مثل إنجاز.',
    'cta': 'دعوة مباشرة وصريحة لبدء التجربة المجانية لمدة 7 أيام، بدون وعود مبالغ فيها.',
    'engagement': 'سؤال حقيقي يدفع القارئ للتعليق والمشاركة، دون ذكر مباشر للنظام في بداية المنشور، ويمكن ذكره بخفة في النهاية أو عدم ذكره إطلاقاً.',
    'sudan_context': 'يتحدث عن واقع التجار في السودان تحديداً: إدارة العمل عن بُعد، النزوح بين المدن، تعدد الفروع الجغرافي، أو تذبذب العملة — دون المبالغة أو افتراض معلومات غير مؤكدة.',
}


def generate_marketing_post(category: str, topic_hint: str = '', existing_posts: list | None = None) -> str:
    """
    Generate ONE ready-to-publish Arabic (Modern Standard Arabic) social media
    post about EnjazIMS itself, for the given marketing category.

    `existing_posts` (optional): content of posts already saved in this same
    category, used to steer the model away from repeating an idea or phrasing
    that's already in the bank. This is a best-effort nudge, not a guarantee —
    callers should still run the result through `find_similar_post()`.

    Used by the platform admin "Marketing / Social Posts" screen — this is
    platform-level content generation, unrelated to any tenant's business data.
    """
    guidance = _MARKETING_CATEGORY_GUIDANCE.get(category, _MARKETING_CATEGORY_GUIDANCE['feature'])

    hint_line = f'\nموضوع أو زاوية مقترحة من طلب المستخدم: {topic_hint}\n' if topic_hint.strip() else ''

    existing_block = ''
    if existing_posts:
        sample = existing_posts[:15]
        numbered = "\n".join(f"{i+1}. {text}" for i, text in enumerate(sample))
        existing_block = (
            "\nمنشورات موجودة بالفعل في نفس هذا التصنيف — لا تكرر نفس الفكرة أو الزاوية أو الصياغة "
            f"الموجودة في أي منها، واكتب شيئاً مختلفاً عنها بوضوح:\n{numbered}\n"
        )

    prompt = (
        f"{_MARKETING_FEATURES_REFERENCE}\n\n"
        f"اكتب منشوراً واحداً فقط لوسائل التواصل الاجتماعي (فيسبوك أو إنستقرام) للترويج لنظام إنجاز.\n"
        f"نوع المنشور المطلوب: {guidance}"
        f"{hint_line}"
        f"{existing_block}\n"
        "قواعد إلزامية:\n"
        "- اكتب بالعربية الفصحى فقط، بدون أي لهجة عامية.\n"
        "- طول المنشور بين 4 و5 أسطر (حوالي 45 إلى 70 كلمة)، فقرة واحدة متصلة بدون عناوين أو نقاط.\n"
        "- لا تخترع أي رقم أو ميزة أو إحصائية غير مذكورة في قائمة الحقائق أعلاه.\n"
        "- لا تذكر أي عميل حقيقي أو شهادة عميل، فالنظام في مرحلة اكتساب أول عملائه.\n"
        "- بدون مصطلحات تقنية معقدة، وبدون صياغة شعرية أو إنشائية مبالغ فيها.\n"
        "- اكتب نص المنشور فقط، دون أي مقدمة أو شرح أو علامات اقتباس حول النص."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "أنت كاتب محتوى تسويقي محترف يكتب بالعربية الفصحى فقط. "
                "تكتب منشورات تسويقية قصيرة ومقنعة بدون مبالغة أو ادعاءات غير مؤكدة، "
                "وتحرص دائماً على ألا يكرر أي منشور جديد فكرة أو صياغة منشور سابق."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    return _call_deepseek(messages, max_tokens=350)


def find_similar_post(text: str, existing_posts: list, threshold: float = 0.72):
    """
    Safety-net duplicate check: compares `text` against each string in
    `existing_posts` using a plain character-based similarity ratio, and
    returns the closest match if it's at or above `threshold` (0..1).

    Returns None if nothing is close enough. This is a blunt, dependency-free
    check meant to catch near-duplicate AI output the prompt-level steering
    missed — not a semantic/meaning-based comparison.
    """
    import difflib

    best_match = None
    best_ratio = 0.0
    for other in existing_posts:
        ratio = difflib.SequenceMatcher(None, text, other).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = other

    if best_match and best_ratio >= threshold:
        return {'content': best_match, 'ratio': round(best_ratio, 2)}
    return None
