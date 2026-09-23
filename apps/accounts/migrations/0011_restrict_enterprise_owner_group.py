from django.db import migrations


def restrict_enterprise_owner_groups(apps, schema_editor):
    """
    يعيد ضبط صلاحيات مجموعة "مدير النشاط" لكل tenant موجود بالفعل في نسخة
    المؤسسات (version_type == 'multi_branch') لتستبعد عمليات الفرع اليومية
    (مبيعات، عملاء، موردين، حسابات ومالية، مناديب...) — نفس القيد المطبّق
    الآن على User.is_tenant_admin عبر apps/accounts/permissions.py::
    get_enterprise_owner_permission_keys. بدون هذا الباكفيل، أي موظف إضافي
    (غير صاحب is_tenant_admin) مُضاف مسبقاً لهذه المجموعة يبقى محتفظاً بكل
    الصلاحيات لأن قراءتها تمر مباشرة عبر PermissionGroup.permissions.
    """
    from apps.accounts.permissions import get_enterprise_owner_permission_keys

    Tenant = apps.get_model('core', 'Tenant')
    PermissionGroup = apps.get_model('accounts', 'PermissionGroup')

    allowed_keys = set(get_enterprise_owner_permission_keys())
    enterprise_tenant_ids = Tenant.objects.filter(version_type='multi_branch').values_list('id', flat=True)

    groups = PermissionGroup.objects.filter(tenant_id__in=list(enterprise_tenant_ids), name='مدير النشاط')
    for group in groups.iterator(chunk_size=200):
        group.permissions = {key: True for key in (group.permissions or {}) if key in allowed_keys}
        group.save(update_fields=['permissions'])


def noop_reverse(apps, schema_editor):
    # لا رجوع تلقائي: الصلاحيات الموسّعة القديمة لا تُستعاد لأن هذا قد يعيد
    # فتح وصول كان مقصوداً إغلاقه؛ يُعاد ضبطها يدوياً إن لزم.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_user_is_branch_supervisor'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(restrict_enterprise_owner_groups, noop_reverse),
    ]
