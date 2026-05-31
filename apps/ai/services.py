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

    now = timezone.now().date()
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
    }


# ──────────────────────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """أنت مساعد أعمال ذكي متخصص في تحليل بيانات المخزون والمبيعات.
تعمل داخل نظام إدارة مخزون (EnjazIMS) لصاحب المحل.
قواعد الرد الصارمة:
- أجب دائماً بالعربية، بأسلوب مهني وموجز.
- استخدم دائماً رمز العملة المُقدَّم في البيانات ولا تُبدّله بعملة أخرى.
- اكتب النص بدون أي تنسيق Markdown: لا نجوم (**) ولا شرطات سفلية ولا علامات # للعناوين.
- استخدم الأرقام والنقاط والعناوين النصية العادية فقط.
- استند إلى الأرقام المُقدَّمة واستنتج منها بشكل منطقي.
- قدّم توصيات عملية قابلة للتنفيذ.
- لا تتجاوز 300 كلمة ما لم يطلب المستخدم تفصيلاً أكثر.
- لا تخترع أرقاماً أو معلومات غير موجودة في السياق."""


def _build_context_message(context: dict) -> str:
    """Format business context as a readable Arabic text block."""
    ctx = context
    cur = ctx.get('currency', '')
    lines = [
        f"📊 بيانات الأعمال ({ctx['period']}) — العملة: {cur}",
        f"  • إيرادات الشهر: {ctx['monthly_revenue']:,.0f} {cur}",
        f"  • إيرادات الأسبوع: {ctx['weekly_revenue']:,.0f} {cur}",
        f"  • عدد الفواتير: {ctx['monthly_invoice_count']}",
        f"  • مشتريات الشهر: {ctx['monthly_purchases']:,.0f} {cur}",
        f"  • مصروفات الشهر: {ctx['monthly_expenses']:,.0f} {cur}",
        f"  • إجمالي الربح: {ctx['gross_profit']:,.0f} {cur}",
    ]

    if ctx['top_selling_items']:
        lines.append("\n🏆 أكثر المنتجات مبيعاً:")
        for i in ctx['top_selling_items']:
            lines.append(f"  • {i['name']}: {i['qty']:.0f} وحدة / {i['revenue']:,.0f} {cur}")

    if ctx['low_stock_items']:
        lines.append("\n⚠️ منتجات تحت الحد الأدنى:")
        for i in ctx['low_stock_items']:
            lines.append(f"  • {i['item']}: {i['current']:.0f} متبقي (الحد: {i['min']:.0f}) — {i['stock']}")

    if ctx['top_debtors']:
        lines.append("\n💳 أعلى أرصدة العملاء:")
        for d in ctx['top_debtors']:
            lines.append(f"  • {d['name']}: {d['balance']:,.0f} {cur}")

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
