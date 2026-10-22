#!/usr/bin/env bash
# يُشغِّل كامل مجموعة الاختبارات (باستثناء اختبار المتصفح — انظر docs/TESTING.md).
# الاستخدام: scripts/test.sh [app_label ...]
#   scripts/test.sh                  → كل التطبيقات
#   scripts/test.sh apps.sales       → تطبيق واحد فقط
#   KEEPDB=0 scripts/test.sh         → إعادة إنشاء قاعدة الاختبار من الصفر
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

KEEPDB_FLAG="--keepdb"
if [ "${KEEPDB:-1}" = "0" ]; then
  KEEPDB_FLAG=""
fi

# apps.core مذكور بوحداته صراحة (بدل apps.core كامل) لتفادي
# apps/core/test_browser_surfaces.py الذي يحتاج Playwright مُثبَّتاً يدوياً
# وغير مُدرَج في requirements.txt عمداً — شغِّله منفصلاً إن لزم (راجع
# docs/TESTING.md).
TARGETS=(
  apps.core.tests apps.core.tests_full apps.core.tests_harness
  apps.core.tests_exchange_rate apps.core.test_system_surfaces apps.core.tests_business_types
  apps.core.tests_tenant_isolation apps.core.tests_plan_limits apps.core.tests_branch_money_scope
  apps.core.tests_assign_orphans apps.core.tests_branch_isolation apps.core.tests_branch_isolation_crawl
  apps.core.tests_branch_manager apps.core.tests_branch_ownership apps.core.tests_branch_treasuries
  apps.core.tests_dashboard_branch_values apps.core.tests_head_office_bank_account apps.core.tests_head_office_treasury
  apps.core.tests_client_survey apps.core.tests_no_dialect apps.core.tests_owner_branch_labels apps.core.tests_pricing
  apps.core.tests_purchasing_mode_lock apps.core.tests_report_scope apps.core.tests_tenant_plan_downgrade
  apps.accounts apps.customers apps.suppliers apps.items
  apps.stocks apps.sales apps.purchases apps.treasury apps.bank_accounts
  apps.data_import apps.agents apps.insurance apps.expenses apps.employees apps.notifications apps.requisitions apps.branch_transfers
)
if [ "$#" -gt 0 ]; then
  TARGETS=("$@")
fi

source .env/bin/activate 2>/dev/null || true
python manage.py test "${TARGETS[@]}" $KEEPDB_FLAG -v 1
