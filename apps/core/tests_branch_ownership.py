"""
اختبار enforce_branch_ownership (apps/core/utils.py) — يمنع IDOR عبر الفروع
على شاشات "سجل واحد بمعرّفه" (اكتُشفت الحاجة له عبر أداة التدقيق
check_branch_scoping؛ راجع خطة تنفيذ Enterprise، القسم 3.4 و10 مخاطرة #2).
"""
from types import SimpleNamespace

from django.http import Http404
from django.test import SimpleTestCase

from apps.core.utils import enforce_branch_ownership


class FakeRequest:
    def __init__(self, branch):
        self.branch = branch


class EnforceBranchOwnershipTests(SimpleTestCase):
    def test_central_admin_bypasses_check(self):
        req = FakeRequest(branch=None)
        obj = SimpleNamespace(branch='other-branch')
        enforce_branch_ownership(req, obj)  # لا يرفع شيئاً

    def test_matching_branch_allowed(self):
        branch = object()
        req = FakeRequest(branch=branch)
        obj = SimpleNamespace(branch=branch)
        enforce_branch_ownership(req, obj)

    def test_mismatched_branch_raises_404(self):
        req = FakeRequest(branch=object())
        obj = SimpleNamespace(branch=object())
        with self.assertRaises(Http404):
            enforce_branch_ownership(req, obj)

    def test_null_branch_on_object_passes_through(self):
        req = FakeRequest(branch=object())
        obj = SimpleNamespace(branch=None)
        enforce_branch_ownership(req, obj)

    def test_indirect_path_matching(self):
        branch = object()
        req = FakeRequest(branch=branch)
        obj = SimpleNamespace(stock=SimpleNamespace(branch=branch))
        enforce_branch_ownership(req, obj, field='stock__branch')

    def test_indirect_path_mismatch_raises(self):
        req = FakeRequest(branch=object())
        obj = SimpleNamespace(stock=SimpleNamespace(branch=object()))
        with self.assertRaises(Http404):
            enforce_branch_ownership(req, obj, field='stock__branch')

    def test_indirect_path_null_intermediate_passes(self):
        req = FakeRequest(branch=object())
        obj = SimpleNamespace(stock=None)
        enforce_branch_ownership(req, obj, field='stock__branch')

    def test_dual_field_allows_if_either_side_matches(self):
        my_branch = object()
        other_branch = object()
        req = FakeRequest(branch=my_branch)
        obj = SimpleNamespace(
            from_stock=SimpleNamespace(branch=other_branch),
            to_stock=SimpleNamespace(branch=my_branch),
        )
        enforce_branch_ownership(req, obj, field=['from_stock__branch', 'to_stock__branch'])

    def test_dual_field_denies_if_neither_side_matches(self):
        req = FakeRequest(branch=object())
        obj = SimpleNamespace(
            from_stock=SimpleNamespace(branch=object()),
            to_stock=SimpleNamespace(branch=object()),
        )
        with self.assertRaises(Http404):
            enforce_branch_ownership(req, obj, field=['from_stock__branch', 'to_stock__branch'])

    def test_dual_field_one_side_null_passes(self):
        req = FakeRequest(branch=object())
        obj = SimpleNamespace(
            from_stock=SimpleNamespace(branch=None),
            to_stock=SimpleNamespace(branch=object()),
        )
        enforce_branch_ownership(req, obj, field=['from_stock__branch', 'to_stock__branch'])
