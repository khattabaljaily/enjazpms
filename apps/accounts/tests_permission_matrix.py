"""
اختبار مصفوفة الصلاحيات: يكتشف تلقائياً كل view محمي بـ
@require_permission / @require_any_permission / @require_all_permissions
(انظر الميتاداتا `_required_permission_keys` التي تضيفها هذه الديكوريترز في
apps/accounts/decorators.py) عبر مسح urlpatterns بالكامل، ولكل واحد منها:

  1. مستخدم بلا أي صلاحيات (PermissionGroup فارغة) → يجب أن يُرفض الوصول.
  2. نفس المستخدم لكن مع مجموعة صلاحيات تمنحه بالضبط المفتاح/المفاتيح
     المطلوبة → يجب ألا يُرفض الوصول (قد ينجح، أو يفشل بسبب لاحق في الـ view
     نفسه كـ 404/405 لعدم وجود كائن حقيقي بمعرّف وهمي — المهم فقط أن فحص
     الصلاحية نفسه لم يكن سبب الرفض).

يمنحنا هذا تغطية تكاد تكون كاملة للمفاتيح الـ145 من ملف واحد، بدل كتابة
اختبار يدوي لكل شاشة على حدة.

ملاحظة: كل الديكوريترز المستخدمة في المشروع تُطبَّق دائماً بترتيب
@login_required ثم @require_permission(...) ثم @require_POST (تم التحقق آلياً
من عدم وجود ترتيب معكوس في كل apps/*/views.py) — أي أن فحص الصلاحية يُنفَّذ
قبل فحص طريقة HTTP دائماً، فيكفي طلب GET لاختبار كل View بصرف النظر عن كونه
POST-only فعلياً.
"""
import re
import uuid

from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

from apps.accounts.models import PermissionGroup, User
from apps.core.test_utils import TenantTestCase

_TOKEN_RE = re.compile(r'<(?:(?P<type>[^:>]+):)?(?P<name>[^>]+)>')

_DUMMY_BY_TYPE = {
    'int': '999999',
    'str': 'test-str',
    'slug': 'test-slug',
    'uuid': str(uuid.uuid4()),
    'path': 'test/path',
}

# مسارات نتجاهلها: أدمن جانغو (له نظام صلاحيات منفصل)، static/media، وأي
# مسار لا نملك تحكماً فيه أو غير مرتبط بتطبيقنا.
_SKIP_PREFIXES = ('admin/', 'static/', 'media/', '__debug__/')


def _discover_permission_protected_routes():
    """يرجع [(path, permission_keys, mode), ...] لكل route محمي بديكوريتر صلاحية."""
    routes = []

    def walk(resolver, prefix):
        for entry in resolver.url_patterns:
            route_piece = str(entry.pattern)
            full_prefix = prefix + route_piece
            if isinstance(entry, URLResolver):
                walk(entry, full_prefix)
            elif isinstance(entry, URLPattern):
                if full_prefix.startswith(_SKIP_PREFIXES):
                    continue
                keys = getattr(entry.callback, '_required_permission_keys', None)
                if not keys:
                    continue
                mode = getattr(entry.callback, '_permission_check_mode', 'all')
                concrete_path = _TOKEN_RE.sub(
                    lambda m: _DUMMY_BY_TYPE.get(m.group('type') or 'str', 'test-str'),
                    full_prefix,
                )
                if not concrete_path.startswith('/'):
                    concrete_path = '/' + concrete_path
                routes.append((concrete_path, tuple(keys), mode))

    walk(get_resolver(), '')
    # كل route فريد بمساره الكامل — قد يظهر نفس الـ view تحت أكثر من اسم مسار
    # (نادر)، فلا داعي لإزالة تكرار عدواني هنا.
    return routes


class PermissionMatrixTests(TenantTestCase):
    # بعض الـ views محمية أيضاً بـ require_capability/require_plan_feature
    # بجانب require_permission (مثلاً وحدة المناديب تتطلب باقة تتيح 'agents').
    # enterprise تفتح كل PLAN_FEATURES، وتفعيل كل TenantCapabilities أدناه
    # يزيل تلك البوابات الإضافية فيعزل الاختبار على فحص مفتاح الصلاحية فقط.
    subscription_plan = 'enterprise'
    version_type = 'multi_branch'

    def setUp(self):
        super().setUp()
        self.no_permission_url = '/no-permission/'
        from apps.core.models import TenantCapabilities
        capability_fields = [
            f.name for f in TenantCapabilities._meta.get_fields()
            if getattr(f, 'name', '').startswith('has_')
        ]
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant, defaults={name: True for name in capability_fields},
        )

    def _make_user(self, suffix, permission_keys=()):
        user = User.objects.create_user(
            username=f'perm-{suffix}', password='secret123',
            tenant=self.tenant, is_tenant_admin=False,
        )
        if permission_keys:
            group = PermissionGroup.objects.create(
                tenant=self.tenant, name=f'group-{suffix}',
                permissions={key: True for key in permission_keys},
                is_active=True,
            )
            user.permission_groups.add(group)
        return user

    def _is_denied(self, response):
        if response.status_code == 403:
            return True
        if response.status_code in (301, 302) and response.url.rstrip('/') == self.no_permission_url.rstrip('/'):
            return True
        return False

    def test_every_permission_protected_route_enforces_its_key(self):
        routes = _discover_permission_protected_routes()
        self.assertGreater(len(routes), 30, 'يجب اكتشاف عدد كبير من المسارات المحمية — تحقق من آلية الاكتشاف نفسها لو فشل هذا')

        checked = 0
        for idx, (path, keys, mode) in enumerate(routes):
            # مُعرِّف فريد لكل تكرار (وليس مرتبطاً بعدد النجاحات) — تجنّباً
            # لتصادم اسم مستخدم لو فشل تكرار سابق واستمر subTest للتالي.
            with self.subTest(path=path, keys=keys):
                denied_user = self._make_user(f'denied-{idx}', permission_keys=())
                self.client.force_login(denied_user)
                response = self.client.get(path)
                self.assertTrue(
                    self._is_denied(response),
                    f'المسار {path} (يتطلب {keys}) لم يُرفض لمستخدم بلا صلاحيات — status={response.status_code}',
                )

                granted_keys = keys if mode == 'all' else keys[:1]
                allowed_user = self._make_user(f'allowed-{idx}', permission_keys=granted_keys)
                self.client.force_login(allowed_user)
                response = self.client.get(path)
                self.assertFalse(
                    self._is_denied(response),
                    f'المسار {path} رُفض رغم منح المستخدم المفتاح المطلوب {granted_keys} — status={response.status_code}',
                )
                checked += 1

        self.assertEqual(checked, len(routes))
