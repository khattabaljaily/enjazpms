"""
AI Views — AJAX endpoints for the chat widget and insights panel.
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission

from .services import chat, generate_daily_insights

logger = logging.getLogger(__name__)


@login_required
@require_permission('use_ai_chat')
@require_POST
def chat_api(request):
    """
    POST /ai/chat/
    Body: {"message": "...", "history": [...]}
    Returns: {"reply": "..."}
    """
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

    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return JsonResponse({"error": "لا يوجد tenant مرتبط بالجلسة"}, status=403)

    try:
        reply = chat(user_message, history, tenant)
    except Exception as exc:
        import traceback
        logger.error("AI chat error: %s\n%s", exc, traceback.format_exc())
        return JsonResponse({"error": f"خطأ داخلي: {exc}"}, status=500)

    return JsonResponse({"reply": reply})


@login_required
@require_permission('view_ai_insights')
@require_GET
def insights_api(request):
    """
    GET /ai/insights/
    Returns: {"insights": "..."}
    """
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return JsonResponse({"error": "لا يوجد tenant مرتبط بالجلسة"}, status=403)

    insights = generate_daily_insights(tenant)
    return JsonResponse({"insights": insights})


@login_required
@require_permission('view_ai_insights')
@require_GET
def advices_api(request):
    """
    GET /ai/advices/
    Returns: {"advices": ["...", "..."]}
    The AI service returns a short multi-point summary; we split it into items.
    """
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return JsonResponse({"error": "لا يوجد tenant مرتبط بالجلسة"}, status=403)

    try:
        raw = generate_daily_insights(tenant)
        # Split into lines and filter empty/short lines
        parts = [p.strip() for p in raw.splitlines() if p.strip()]
        # If result is a single paragraph, split by common separators
        if len(parts) <= 1:
            # try splitting by numbered bullets or '•' or '•'
            for sep in ['•', '-', '\u2022']:
                if sep in raw:
                    parts = [p.strip() for p in raw.split(sep) if p.strip()]
                    break
            # fallback split by sentences
            if len(parts) <= 1:
                parts = [s.strip() for s in raw.replace('\n', ' ').split('  ') if s.strip()]

        # Final cleanup: remove numeric prefixes like '1.' or '١)'
        import re
        cleaned = []
        for p in parts:
            p2 = re.sub(r'^[0-9]+[\.).\s]+', '', p)
            p2 = re.sub(r'^[٠-٩]+[\).\s]+', '', p2)
            if len(p2) > 5:
                cleaned.append(p2)

        # Provide fallback advices when AI returns nothing useful
        if not cleaned:
            cleaned = [
                'راجع الأصناف ذات المخزون المنخفض وأعد طلب المخزون الضروري.',
                'تابع أعلى 5 عملاء غير المسددين وحاول تحصيل المبالغ المستحقة.',
                'راجع أفضل المنتجات هذا الشهر وفكر في ترويج للمنتجات الأبطأ مبيعاً.',
            ]

        return JsonResponse({"advices": cleaned})
    except Exception as exc:
        import traceback
        logger.error("AI advices error: %s\n%s", exc, traceback.format_exc())
        return JsonResponse({"advices": [
            'تعذّر توليد نصائح ذكية في الوقت الحالي. حاول مرة لاحقة.'
        ]})
