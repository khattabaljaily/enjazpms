"""نطاق المخازن المسموح بها لتحديد الكميات الافتتاحية."""
from .models import Stock


def opening_balance_stocks(tenant, branch):
    """
    المخازن النشطة التي يجوز لهذا المستخدم تحديد كمياتها الافتتاحية.

    مستخدم الفرع: مخازن فرعه فقط. مدير النشاط في نسخة المؤسسات (بلا فرع): المخزن المركزي والمخازن بلا فرع
    فقط، فالكميات الافتتاحية لمخازن الفروع من اختصاص الفرع نفسه. غير المؤسسات: كل مخازن المشترك كما كان.
    """
    qs = Stock.objects.for_tenant(tenant).filter(is_active=True)
    if branch is not None:
        return qs.for_branch(branch)
    if tenant.is_enterprise():
        return qs.filter(branch__isnull=True)
    return qs
