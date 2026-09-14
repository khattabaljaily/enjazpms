# تشغيل الاختبارات

```bash
source .env/bin/activate

# كل الاختبارات (باستثناء المتصفح، انظر أدناه)
python manage.py test

# تطبيق واحد
python manage.py test apps.sales

# أسرع أثناء التطوير — لا يعيد إنشاء قاعدة بيانات الاختبار في كل مرة
python manage.py test --keepdb
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
