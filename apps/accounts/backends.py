"""
تسجيل الدخول باسم المستخدم أو البريد الإلكتروني.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """
    يجرّب اسم المستخدم أولاً (السلوك الأصلي)، فإن لم يوجد وكان المُدخل بريداً
    إلكترونياً يبحث بالبريد (دون حساسية لحالة الأحرف).

    البريد فريد داخل المشترك الواحد فقط، فقد يتكرر بين مشتركين؛ في هذه الحالة
    يُقبل الدخول فقط إن طابقت كلمة المرور حساباً واحداً بالضبط.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or password is None:
            return None
        username = username.strip()

        try:
            user = UserModel._default_manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            user = None

        if user is not None:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
            return None

        if '@' not in username:
            # تشغيل الـ hasher لتقليل فرق التوقيت بين حساب موجود وغير موجود
            UserModel().set_password(password)
            return None

        candidates = list(UserModel._default_manager.filter(email__iexact=username, is_active=True))
        if not candidates:
            UserModel().set_password(password)
            return None
        matches = [u for u in candidates if u.check_password(password) and self.user_can_authenticate(u)]
        return matches[0] if len(matches) == 1 else None
