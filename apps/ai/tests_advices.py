"""النصائح الذكية: تنظيف مخرجات النموذج (مقدمات، رموز قوائم)، وتعليمات تطلب نقاطاً قصيرة، وشريط من سطرين."""
from unittest import mock

from django.urls import reverse

from apps.ai import services
from apps.core.test_utils import TenantTestCase

RAW = """فرضية العمل: بما أن النشاط شركة توزيع أدوية تعمل بنظام الفروع فإن الأرقام الحالية تعني غياب النشاط لا نظافة الالتزامات.
1. راجع الأصناف ذات المخزون المنخفض.
- **تابع** العملاء غير المسددين.
• ٢) حصّل المبالغ المستحقة من أكبر عميل.
قصير"""


class AdvicesCleaningTests(TenantTestCase):
    subscription_plan = 'enterprise'
    version_type = 'multi_branch'

    def test_preamble_and_list_markers_are_removed(self):
        with mock.patch('apps.ai.views.generate_daily_insights', return_value=RAW):
            resp = self.client.get(reverse('ai:advices'))
        self.assertEqual(resp.status_code, 200)
        advices = resp.json()['advices']
        self.assertEqual(advices, ['راجع الأصناف ذات المخزون المنخفض.', 'تابع العملاء غير المسددين.',
                                   'حصّل المبالغ المستحقة من أكبر عميل.'])
        for advice in advices:
            self.assertNotIn('فرضية', advice)
            self.assertNotIn('**', advice)


class TipPromptTests(TenantTestCase):
    def test_prompt_demands_short_points_without_preamble(self):
        with mock.patch.object(services, '_call_deepseek', return_value='x') as call, \
                mock.patch.object(services, 'collect_business_context', return_value={}), \
                mock.patch.object(services, '_build_context_message', return_value='ctx'), \
                mock.patch.object(services, '_build_system_prompt', return_value='sys'):
            services.generate_daily_insights(self.tenant, self.user)
        user_message = call.call_args[0][0][1]['content']
        self.assertIn('لا تتجاوز 120 حرفاً', user_message)
        self.assertIn('فرضية عمل', user_message)


class AdviceWidgetStylesTests(TenantTestCase):
    def test_widget_wraps_to_two_lines_instead_of_single_line_ellipsis(self):
        from pathlib import Path
        from django.conf import settings
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'ai_advices.css').read_text(encoding='utf-8')
        line = next(l for l in css.splitlines() if l.startswith('.ai-advices .ai-adv-text{'))
        self.assertIn('-webkit-line-clamp:2', line)
        self.assertNotIn('white-space:nowrap', line)
