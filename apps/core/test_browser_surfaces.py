from django.conf import settings
from django.test import LiveServerTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import BusinessType, Tenant, TenantCapabilities
from apps.items.models import Category


class BrowserSurfaceTests(LiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        business_type = BusinessType.objects.create(
            name='browser-retail', name_ar='اختبار المتصفح', slug='browser-retail'
        )
        cls.tenant = Tenant.objects.create(
            name='Browser Surface Tenant', slug='browser-surface-tenant',
            business_type=business_type, subscription_plan='enterprise',
            version_type='multi_branch', max_stocks=20, max_branches=20,
            currency='SDG', terms_accepted_at=timezone.now(),
            terms_version=settings.TERMS_VERSION,
        )
        TenantCapabilities.objects.update_or_create(
            tenant=cls.tenant, defaults={'has_insurance_billing': True}
        )
        Category.objects.create(tenant=cls.tenant, name='تصنيف المتصفح')
        cls.user = User.objects.create_user(
            username='browser-surface-user', password='secret123',
            tenant=cls.tenant, is_tenant_admin=True,
        )

    def test_help_and_training_panels_open_in_real_browser(self):
        from playwright.sync_api import sync_playwright
        from urllib.parse import urlparse

        client = self.client_class()
        self.assertTrue(client.login(username='browser-surface-user', password='secret123'))
        session_cookie = client.cookies['sessionid'].value
        live_host = urlparse(self.live_server_url).hostname

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies([{
                'name': 'sessionid',
                'value': session_cookie,
                'domain': live_host,
                'path': '/',
            }])
            page = context.new_page()
            page.goto(f'{self.live_server_url}/data-import/products/', wait_until='networkidle')

            self.assertTrue(page.locator('#training-fab').is_visible())
            self.assertTrue(page.locator('#help-fab').is_visible())
            page.wait_for_selector('#productImportFile')
            self.assertTrue(page.locator('#productImportFile').is_visible())

            page.locator('#training-fab').click()
            self.assertTrue(page.locator('#training-panel.is-open').is_visible())
            self.assertIn('استيراد المنتجات', page.locator('#training-panel').inner_text())

            page.locator('#help-fab').click()
            self.assertTrue(page.locator('#help-panel.open').is_visible())
            self.assertIn('مركز المساعدة', page.locator('#help-panel').inner_text())

            page.goto(f'{self.live_server_url}/reports/sales/summary/', wait_until='networkidle')
            self.assertIn('تقرير', page.locator('body').inner_text())

            browser.close()
