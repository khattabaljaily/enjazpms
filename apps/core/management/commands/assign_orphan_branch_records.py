"""
assign_orphan_branch_records — ينسب السجلات القديمة التي بلا فرع (branch=NULL)
إلى فروعها في نسخة المؤسسات.

لماذا: بعد عزل الفروع صار العملاء والموردون والمناديب والموظفون والمصروفات
خاصة بفرع واحد، فالسجل بلا فرع لا يراه أي مستخدم فرع (يراه المستخدم المركزي
فقط). هذا الأمر يعيد نسبتها:
  - عميل/مورد/مندوب: إلى الفرع الذي تمت فيه أغلب فواتيره (عبر مخزن الفاتورة)،
    فإن لم تكن له فواتير فإلى --branch (أو الفرع الافتراضي للمشترك).
  - موظف/مصروف: إلى --branch (أو الفرع الافتراضي).

الوضع الافتراضي معاينة فقط (لا يكتب شيئاً). أضف --apply للتنفيذ الفعلي.

    python manage.py assign_orphan_branch_records --tenant 3
    python manage.py assign_orphan_branch_records --tenant 3 --branch 7 --apply
"""
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Branch, Tenant


class Command(BaseCommand):
    help = 'ينسب السجلات القديمة بلا فرع إلى فروعها (معاينة افتراضياً، --apply للتنفيذ).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=int, help='معرّف المشترك (الكل إن لم يُحدَّد)')
        parser.add_argument('--branch', type=int, help='فرع بديل للسجلات التي لا يمكن استنتاج فرعها')
        parser.add_argument('--apply', action='store_true', help='نفّذ التعديلات فعلاً')

    def handle(self, *args, **opts):
        tenants = Tenant.objects.filter(version_type='multi_branch')
        if opts['tenant']:
            tenants = tenants.filter(pk=opts['tenant'])
        if not tenants.exists():
            raise CommandError('لا يوجد مشترك مطابق بنسخة المؤسسات.')
        for tenant in tenants:
            self._handle_tenant(tenant, opts['branch'], opts['apply'])
        if not opts['apply']:
            self.stdout.write(self.style.WARNING('معاينة فقط — لم يُكتب شيء. أضف --apply للتنفيذ.'))

    def _fallback_branch(self, tenant, branch_id):
        qs = Branch.objects.filter(tenant=tenant, is_active=True)
        if branch_id:
            branch = qs.filter(pk=branch_id).first()
            if not branch:
                raise CommandError(f'الفرع {branch_id} غير موجود/غير نشط لهذا المشترك.')
            return branch
        return qs.filter(is_default=True).first() or qs.order_by('id').first()

    @staticmethod
    def _majority(branch_ids):
        counts = Counter(b for b in branch_ids if b)
        return counts.most_common(1)[0][0] if counts else None

    def _handle_tenant(self, tenant, branch_id, apply):
        from apps.agents.models import Agent
        from apps.customers.models import Customer
        from apps.employees.models import Employee
        from apps.expenses.models import Expense
        from apps.purchases.models import PurchaseInvoice
        from apps.sales.models import SaleInvoice
        from apps.suppliers.models import Supplier

        fallback = self._fallback_branch(tenant, branch_id)
        self.stdout.write(f'\n== {tenant.name} (id={tenant.pk}) — الفرع البديل: {fallback or "لا يوجد"} ==')

        # (الموديل، دالة تستنتج الفرع من فواتير السجل أو None)
        plan = [
            (Customer, lambda o: self._majority(
                SaleInvoice.objects.filter(tenant=tenant, customer=o).values_list('stock__branch', flat=True))),
            (Supplier, lambda o: self._majority(
                PurchaseInvoice.objects.filter(tenant=tenant, supplier=o).values_list('stock__branch', flat=True))),
            (Agent, lambda o: self._majority(
                SaleInvoice.objects.filter(tenant=tenant, agent=o).values_list('stock__branch', flat=True))),
            (Employee, None),
            (Expense, None),
        ]
        with transaction.atomic():
            for model, infer in plan:
                orphans = model.unscoped.filter(tenant=tenant, branch__isnull=True)
                inferred = fallback_used = skipped = 0
                for obj in orphans:
                    target_id = infer(obj) if infer else None
                    if target_id:
                        inferred += 1
                    elif fallback:
                        target_id = fallback.pk
                        fallback_used += 1
                    else:
                        skipped += 1
                        continue
                    if apply:
                        model.unscoped.filter(pk=obj.pk).update(branch_id=target_id)
                self.stdout.write(
                    f'  {model._meta.verbose_name_plural}: {inferred} من الفواتير، '
                    f'{fallback_used} للفرع البديل، {skipped} متروك'
                )
            if not apply:
                transaction.set_rollback(True)
