"""
AI Views — AJAX endpoints for the chat widget and insights panel.
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required

from .services import chat, generate_daily_insights

logger = logging.getLogger(__name__)


@login_required
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
