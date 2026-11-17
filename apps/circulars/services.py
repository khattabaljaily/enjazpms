"""نشر التعاميم: نقطة واحدة تمر بها كل الأحداث الحساسة.

القائمة (EVENTS) هي المرجع: لكل حدث مفتاح ثابت ومستوى وعنوان. لا تُرمى استثناءات من هنا
أبدًا، فالتعميم لا يجوز أن يعطّل العملية الأصلية.
"""
import logging

from .models import Circular

logger = logging.getLogger(__name__)

RED, YELLOW = Circular.LEVEL_RED, Circular.LEVEL_YELLOW

# مفتاح الحدث ← (المستوى، العنوان)
EVENTS = {
    # المخزن
    'stock.opening_edit': (RED, 'تعديل كمية افتتاحية'),
    'stock.stocktake_shortage': (RED, 'تسوية جرد بالنقص'),
    'stock.destruction': (YELLOW, 'إتلاف مخزون'),
    'item.cost_edit': (RED, 'تعديل تكلفة صنف'),
    'item.price_adjust': (RED, 'تعديل عام لأسعار الأصناف'),
    'item.delete': (RED, 'حذف صنف له رصيد'),
    # المالية
    'agent.opening_edit': (RED, 'تعديل رصيد افتتاحي لمندوب'),
    'customer.opening_edit': (RED, 'تعديل رصيد افتتاحي لعميل'),
    'supplier.opening_edit': (RED, 'تعديل رصيد افتتاحي لمورد'),
    'sale.invoice_cancel': (RED, 'إلغاء فاتورة مبيعات'),
    'sale.invoice_edit': (RED, 'تعديل فاتورة مبيعات مؤكدة'),
    'payment.customer_cancel': (RED, 'إلغاء دفعة عميل'),
    'payment.supplier_cancel': (RED, 'إلغاء دفعة مورد'),
    'expense.cancel': (YELLOW, 'إلغاء مصروف'),
    # المبيعات والمشتريات
    'sale.below_cost': (RED, 'بيع بأقل من التكلفة'),
    'sale.big_discount': (YELLOW, 'خصم كبير على فاتورة'),
    'purchase.order_cancel': (YELLOW, 'إلغاء أمر شراء'),
    # الأشخاص والصلاحيات
    'user.create': (RED, 'إضافة مستخدم'),
    'user.update': (RED, 'تعديل مستخدم أو صلاحياته'),
    'user.delete': (RED, 'حذف مستخدم'),
    'permission_group.create': (RED, 'إنشاء مجموعة صلاحيات'),
    'permission_group.update': (RED, 'تعديل مجموعة صلاحيات'),
    'permission_group.delete': (RED, 'حذف مجموعة صلاحيات'),
    'employee.salary_edit': (RED, 'تعديل راتب موظف'),
    'employee.salary_cancel': (YELLOW, 'إلغاء كشف راتب'),
    'employee.advance_cancel': (YELLOW, 'إلغاء سلفة موظف'),
    # النظام
    'system.settings_edit': (RED, 'تعديل إعدادات النظام'),
    'system.import': (YELLOW, 'استيراد بيانات'),
}

# حدود افتراضية (تُضبط من settings.py بمفاتيح CIRCULAR_*)
def _threshold(name, default):
    from django.conf import settings
    return getattr(settings, name, default)


def discount_threshold():
    """نسبة الخصم (%) التي يُنشر فوقها تعميم."""
    return _threshold('CIRCULAR_DISCOUNT_PERCENT', 10)


def _actor_name(user):
    if not user:
        return ''
    return (user.get_full_name() or getattr(user, 'username', '') or '').strip()


def publish_circular(request=None, *, event_key, message, tenant=None, user=None, branch=None,
                     object_label='', link='', before=None, after=None, level=None, title=None):
    """ينشر تعميمًا. يأخذ المشترك والمستخدم والفرع من الطلب إن لم تُمرَّر صراحة."""
    try:
        if request is not None:
            tenant = tenant or getattr(request, 'tenant', None)
            user = user or getattr(request, 'user', None)
            if branch is None:
                branch = getattr(request, 'branch', None)
        if not tenant:
            return None
        if user is not None and not getattr(user, 'is_authenticated', False):
            user = None
        default_level, default_title = EVENTS.get(event_key, (RED, event_key))
        return Circular.objects.create(
            tenant=tenant,
            branch=branch,
            level=level or default_level,
            event_key=event_key,
            title=title or default_title,
            message=message,
            actor=user,
            actor_name=_actor_name(user),
            object_label=object_label,
            link=link,
            before=before or {},
            after=after or {},
            created_by=user,
        )
    except Exception:
        logger.exception('تعذّر نشر التعميم %s', event_key)
        return None


def visible_circulars(request):
    """مستخدم الفرع يرى تعاميم فرعه والتعاميم العامة؛ غيره يرى الكل."""
    from django.db.models import Q
    tenant = getattr(request, 'tenant', None)
    qs = Circular.objects.filter(tenant=tenant) if tenant else Circular.objects.none()
    branch = getattr(request, 'branch', None)
    if branch is not None:
        qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
    return qs


def unread_count(request):
    qs = visible_circulars(request)
    return qs.exclude(reads__user=request.user).count()


# ── أدوات مساعدة لنقاط الربط ───────────────────────────────────────────────

def who(request):
    user = getattr(request, 'user', None)
    return _actor_name(user) or 'مستخدم'


def text(value):
    """قيمة بنص مقروء: أرقام بلا أصفار زائدة، وفراغ يصير «—»."""
    from decimal import Decimal
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'نعم' if value else 'لا'
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f')
    return str(value)


def snap(obj, labels):
    """لقطة حقول الكائن (قبل التعديل: خذها قبل أن يعدّل النموذج الكائن)."""
    return {f: text(getattr(obj, f, None)) for f in labels}


def publish_changes(request, event_key, obj, before, labels, noun, **kwargs):
    """ينشر تعميمًا إن تغيّر أي حقل من labels بين before والحالة الحالية لـ obj."""
    after = snap(obj, labels)
    changed = [f for f in labels if before.get(f) != after.get(f)]
    if not changed:
        return None
    parts = [f'{labels[f]}: {before.get(f)} ← {after[f]}' for f in changed]
    return publish_circular(
        request, event_key=event_key, object_label=str(obj),
        message=f'عدّل {who(request)} {noun} «{obj}» — ' + '؛ '.join(parts),
        before={f: before.get(f) for f in changed}, after={f: after[f] for f in changed}, **kwargs)


def publish_simple(request, event_key, noun, label, extra='', **kwargs):
    """تعميم لإجراء بلا قيمة قبل/بعد (إلغاء، حذف، إنشاء...): «فعل {who} {noun} «{label}»»."""
    msg = f'{who(request)}: {noun} «{label}»' + (f' — {extra}' if extra else '')
    return publish_circular(request, event_key=event_key, object_label=str(label), message=msg, **kwargs)
