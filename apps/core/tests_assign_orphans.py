from io import StringIO

from django.core.management import call_command

from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_customer, make_employee


class AssignOrphanBranchRecordsTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع 1')
        self.c = make_customer(self.tenant, name='قديم')
        self.e = make_employee(self.tenant, name='موظف قديم')

    def _run(self, *args):
        call_command('assign_orphan_branch_records', '--tenant', str(self.tenant.pk), *args, stdout=StringIO())

    def test_dry_run_changes_nothing(self):
        self._run()
        self.c.refresh_from_db()
        self.assertIsNone(self.c.branch_id)

    def test_apply_uses_default_branch_when_no_invoices(self):
        self._run('--apply')
        self.c.refresh_from_db()
        self.e.refresh_from_db()
        self.assertEqual((self.c.branch_id, self.e.branch_id), (self.b1.id, self.b1.id))
