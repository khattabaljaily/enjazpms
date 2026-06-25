"""
AI Views — AJAX endpoints for the chat widget and insights panel.
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from apps.accounts.activity_service import log_activity

from .services import chat, generate_daily_insights

logger = logging.getLogger(__name__)


def _plan_allows_ai(tenant) -> bool:
    return tenant is not None and tenant.plan_allows('ai_assistant')


@login_required
@require_permission('use_ai_chat')
@require_POST
def chat_api(request):
    tenant = getattr(request, 'tenant', None)
    if not _plan_allows_ai(tenant):
        return JsonResponse({"error": "المساعد الذكي متاح للباقة الاحترافية فما فوق"}, status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "طلب غير صالح"}, status=400)

    user_message = (body.get("message") or "").strip()
    if not user_message:
        return JsonResponse({"error": "الرسالة فارغة"}, status=400)

    history = body.get("history") or []
    if not isinstance(history, list):
        history = []

    try:
        reply = chat(user_message, history, tenant)
    except Exception as exc:
        import traceback
        logger.error("AI chat error: %s\n%s", exc, traceback.format_exc())
        return JsonResponse({"error": f"خطأ داخلي: {exc}"}, status=500)

    preview = user_message[:150] + ('...' if len(user_message) > 150 else '')
    log_activity(request, 'استخدام المساعد الذكي', f'السؤال: {preview}', 'other')
    return JsonResponse({"reply": reply})


@login_required
@require_permission('view_ai_insights')
@require_GET
def insights_api(request):
    tenant = getattr(request, 'tenant', None)
    if not _plan_allows_ai(tenant):
        return JsonResponse({"error": "الرؤى الذكية متاحة للباقة الاحترافية فما فوق"}, status=403)

    insights = generate_daily_insights(tenant)
    log_activity(request, 'عرض الرؤى الذكية اليومية', '', 'other')
    return JsonResponse({"insights": insights})


@login_required
@require_permission('view_ai_insights')
@require_GET
def advices_api(request):
    tenant = getattr(request, 'tenant', None)
    if not _plan_allows_ai(tenant):
        return JsonResponse({"error": "النصائح الذكية متاحة للباقة الاحترافية فما فوق"}, status=403)

    try:
        raw = generate_daily_insights(tenant)
        parts = [p.strip() for p in raw.splitlines() if p.strip()]
        if len(parts) <= 1:
            for sep in ['•', '-', '•']:
                if sep in raw:
                    parts = [p.strip() for p in raw.split(sep) if p.strip()]
                    break
            if len(parts) <= 1:
                parts = [s.strip() for s in raw.replace('\n', ' ').split('  ') if s.strip()]

        import re
        cleaned = []
        for p in parts:
            p2 = re.sub(r'^[0-9]+[\.).\s]+', '', p)
            p2 = re.sub(r'^[٠-٩]+[\).\s]+', '', p2)
            if len(p2) > 5:
                cleaned.append(p2)

        if not cleaned:
            cleaned = [
                'راجع الأصناف ذات المخزون المنخفض وأعد طلب المخزون الضروري.',
                'تابع أعلى 5 عملاء غير المسددين وحاول تحصيل المبالغ المستحقة.',
                'راجع أفضل المنتجات هذا الشهر وفكر في ترويج للمنتجات الأبطأ مبيعاً.',
            ]

        log_activity(request, 'عرض النصائح الذكية', '', 'other')
        return JsonResponse({"advices": cleaned})
    except Exception as exc:
        import traceback
        logger.error("AI advices error: %s\n%s", exc, traceback.format_exc())
        return JsonResponse({"advices": [
            'تعذّر توليد نصائح ذكية في الوقت الحالي. حاول مرة لاحقة.'
        ]})


@login_required
@require_POST
def track_open_api(request):
    """Called from JS when user opens the AI chat panel."""
    try:
        body = json.loads(request.body)
        page = (body.get('page') or '')[:200]
    except (json.JSONDecodeError, ValueError):
        page = ''
    log_activity(request, 'فتح المساعد الذكي', f'الصفحة: {page}' if page else '', 'other')
    return JsonResponse({'ok': True})
