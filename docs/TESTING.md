# تشغيل الاختبارات

```bash
# الطريقة الموصى بها — كل الاختبارات (باستثناء المتصفح، انظر أدناه)
scripts/test.sh

# تطبيق واحد
scripts/test.sh apps.sales

# إعادة إنشاء قاعدة بيانات الاختبار من الصفر (افتراضياً يُستخدم --keepdb)
KEEPDB=0 scripts/test.sh

# أو مباشرة عبر manage.py
source .env/bin/activate
python manage.py test apps.sales --keepdb
```

يحتاج الأمر قاعدة MySQL حقيقية يمكن الوصول إليها بنفس بيانات `secrets.json`
(نفس محرك قاعدة بيانات الإنتاج — لا يوجد بديل SQLite). عند أول تشغيل فقط
سيُنشئ Django قاعدة `test_enjazpms` تلقائياً.

## اختبار المتصفح (Playwright)

`apps/core/test_browser_surfaces.py` يحتاج Playwright مُثبَّتاً يدوياً (غير
موجود في `requirements.txt` عمداً — لا يُشغَّل ضمن `python manage.py test`
العادي على بيئة نظيفة):

```bash
pip install playwright
playwright install chromium
python manage.py test apps.core.test_browser_surfaces
```

## بنية الاختبار المشتركة

كل اختبار جديد يتعامل مع منطق أعمال (فواتير، مخزون، خزينة...) يجب أن يرث من
`TenantTestCase` في [`apps/core/test_utils.py`](../apps/core/test_utils.py)
بدل تكرار إنشاء `BusinessType`/`Tenant`/`User` يدوياً:

```python
from apps.core.test_utils import TenantTestCase, make_item

class MyFlowTests(TenantTestCase):
    version_type = 'multi_stock'       # افتراضي: single_store
    subscription_plan = 'pro'          # افتراضي: basic

    def test_something(self):
        item = make_item(self.tenant, name='...')
        self.set_quantity(item, self.default_stock, '10')
        # self.tenant / self.user / self.client (مسجّل دخول) جاهزون
        ...
```

`apps/core/tests_harness.py` اختبار ذاتي لهذا الأساس نفسه — إذا فشل فالمشكلة
في البنية المشتركة، وليس في منطق عمل.

## ملاحظات عن ملفات اختبار خاصة

- `apps/accounts/tests_permission_matrix.py` يكتشف تلقائياً كل view محمي
  بصلاحية عبر مسح urlpatterns بالكامل (~390 مساراً) ويفحص كل واحد بطلبين
  HTTP فعليين — أبطأ من بقية الاختبارات (دقيقتان تقريباً). طبيعي.
- `apps/sales/tests_concurrency.py` يستخدم `TransactionTestCase` (وليس
  `TestCase`) لأنه يحتاج threads باتصالات قاعدة بيانات منفصلة فعلياً لاختبار
  أقفال `select_for_update` تحت تنافس حقيقي — لا ترثه من `TenantTestCase`.
- `apps/core/test_browser_surfaces.py` (Playwright) مُستبعد عمداً من
  `scripts/test.sh` — شغِّله يدوياً حسب القسم أعلاه.
