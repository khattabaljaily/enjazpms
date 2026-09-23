from django.db import migrations


def backfill_is_owner_group(apps, schema_editor):
    """
    قبل هذه الهجرة، مجموعة "مدير النشاط" التلقائية (create_owner_group) لم
    تكن مميَّزة بأي علامة تفرّقها عن مجموعة عادية — فكانت تظهر قابلة
    للإسناد لأي مستخدم عادي من شاشة إضافة/تعديل مستخدم، رغم أنها تحمل كل
    صلاحيات مالك الاشتراك. نعلّم كل مجموعة موجودة فعلاً بهذا الاسم بحقل
    is_owner_group=True الجديد حتى تُستبعد من تلك الشاشة بأثر رجعي أيضاً.
    """
    PermissionGroup = apps.get_model('accounts', 'PermissionGroup')
    PermissionGroup.objects.filter(name='مدير النشاط').update(is_owner_group=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_permissiongroup_is_owner_group'),
    ]

    operations = [
        migrations.RunPython(backfill_is_owner_group, noop_reverse),
    ]
