"""
صفحة «استيراد البيانات» الموحّدة: التصنيفات، المنتجات، العملاء، الموردون، المناديب.
شاشات القوائم نفسها لا تحمل أي زر/نافذة استيراد (التصدير فقط).
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.test_utils import TenantTestCase
from apps.items.models import Category


class HubCardsTests(TenantTestCase):
    subscription_plan = 'pro'

    def _hub(self):
        return self.client.get(reverse('data_import:hub'))

    def test_hub_lists_all_five_in_order(self):
        self.tenant.capabilities.has_agents_module = True
        self.tenant.capabilities.save()
        html = self._hub().content.decode()
        order = [html.find(t) for t in (
            'استيراد التصنيفات', 'استيراد المنتجات', 'استيراد العملاء', 'استيراد الموردين', 'استيراد المناديب')]
        self.assertTrue(all(i > 0 for i in order), order)
        self.assertEqual(order, sorted(order))

    def test_agents_card_hidden_without_agents_module(self):
        self.tenant.capabilities.has_agents_module = False
        self.tenant.capabilities.save()
        self.assertNotContains(self._hub(), 'استيراد المناديب')
        self.assertNotEqual(self.client.get(reverse('data_import:agent_page')).status_code, 200)


class BasicPlanAgentsTests(TenantTestCase):
    subscription_plan = 'basic'

    def test_agents_hidden_on_basic_plan(self):
        self.assertNotContains(self.client.get(reverse('data_import:hub')), 'استيراد المناديب')
        self.assertNotEqual(self.client.get(reverse('data_import:agent_page')).status_code, 200)


class ListScreensHaveNoImportTests(TenantTestCase):
    subscription_plan = 'pro'

    def test_category_and_agent_lists_have_no_import_ui(self):
        self.tenant.capabilities.has_agents_module = True
        self.tenant.capabilities.save()
        cats = self.client.get(reverse('items:category_list'))
        self.assertNotContains(cats, 'importCategoriesModal')
        self.assertNotContains(cats, 'btnOpenImportCategories')
        self.assertContains(cats, 'export_categories')
        agents = self.client.get(reverse('agents:list'))
        self.assertNotContains(agents, 'importAgentsModal')
        self.assertContains(agents, 'btnExportAgents')


class ImportCommitTests(TenantTestCase):
    subscription_plan = 'pro'

    def test_category_import_through_hub_page(self):
        page = self.client.get(reverse('data_import:category_page'))
        self.assertContains(page, reverse('items:category_import_api'))
        csv = 'الاسم,التصنيف الرئيسي\nمشروبات,\nعصائر,مشروبات\n'.encode('utf-8')
        resp = self.client.post(reverse('items:category_import_api'),
                                {'file': SimpleUploadedFile('c.csv', csv)})
        self.assertEqual(resp.json()['imported'], 2)
        self.assertEqual(Category.objects.get(tenant=self.tenant, name='عصائر').parent.name, 'مشروبات')

    def test_agent_import_through_hub_page(self):
        self.tenant.capabilities.has_agents_module = True
        self.tenant.capabilities.save()
        page = self.client.get(reverse('data_import:agent_page'))
        self.assertContains(page, reverse('agents:import_api'))
        csv = 'الاسم,الهاتف\nأحمد,0912345678\n'.encode('utf-8')
        resp = self.client.post(reverse('agents:import_api'), {'file': SimpleUploadedFile('a.csv', csv)})
        self.assertEqual(resp.json()['imported'], 1)
