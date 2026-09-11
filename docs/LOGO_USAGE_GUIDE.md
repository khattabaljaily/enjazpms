# 🎨 دليل هوية ENJAZ PMS

الهوية البصرية عبارة عن علامة (Logo mark) هندسية بأحرف عربية متداخلة + Wordmark نصي **ENJAZ**. ملفات العلامة في `static/img/logo/`: `enjaz-mark-dark.png` (للخلفيات الفاتحة)، `enjaz-mark-light.png` (أبيض، للخلفيات الداكنة/الملوّنة)، و`enjaz-mark-gold.png` (نسخة بلون الشركة، احتياطية).

## المكوّن (CSS)

معرّف في `static/css/main.css`:

```html
<span class="brand-mark">
    <img src="{% static 'img/logo/enjaz-mark-dark.png' %}" alt="" class="brand-mark__icon brand-mark__icon--dark">
    <img src="{% static 'img/logo/enjaz-mark-light.png' %}" alt="" class="brand-mark__icon brand-mark__icon--light">
    <span class="brand-wordmark">ENJAZ</span>
</span>
```

- `.brand-mark` — الحاوية (أيقونة + نص)، تعرض الأيقونة المناسبة تلقائياً حسب `data-theme`.
- `.brand-mark--inverse` — يفرض الأيقونة البيضاء بغض النظر عن الوضع، للخلفيات ذات لون ثابت (مثل لوحة تسجيل الدخول الجانبية).
- `.brand-mark--lg` — حجم أيقونة أكبر عند الحاجة لإبراز أوضح.
- `.brand-wordmark` — النص "ENJAZ"، يستخدم `var(--text-primary)` فيتكيّف تلقائياً مع الوضع الفاتح والداكن.
- `.brand-wordmark--inverse` / `.brand-wordmark--lg` — نسخة بيضاء / حجم أكبر للنص، بنفس منطق الأيقونة.

## أماكن الاستخدام

| المكان | الملف |
|---|---|
| الشريط العلوي (Navbar) | `apps/core/templates/components/navbar.html` |
| القائمة الجانبية (Sidebar) | `apps/core/templates/components/sidebar.html` |
| صفحات الدخول/التسجيل | `apps/core/templates/layouts/auth.html` |
| صفحة "عن النظام" | `apps/core/templates/core/about.html` |
| صفحة الأسعار | `apps/core/templates/core/pricing.html` |
| اتفاقية الاستخدام | `apps/core/templates/core/terms_of_service.html` (نسخة محلية من الـ CSS داخل الصفحة نفسها) |

## أيقونات التطبيق (Favicon / PWA)

نظراً لأن المتصفحات تتطلب ملف صورة فعلي للأيقونة (لا يمكن استخدام نص مباشرة)، تُستخدم علامة بسيطة بحرف **E** على خلفية كهرمانية `#c9840f`، مولّدة في `static/img/icons/` بالمقاسات المطلوبة لِـ `manifest.json` والـ favicon. المصدر القابل للتعديل: `static/img/icons/icon-source.svg`.

## مستند طباعة الفواتير/العروض

عند عدم رفع المنشأة لشعارها الخاص (`tenant.logo`)، تُعرض بدلاً من الشعار علامة مربعة صغيرة بأول حرف من اسم المنشأة (`.print-logo-mark` في `static/css/print_invoice.css` و`print_quote.css`) — بنفس المنطق المستخدم في قالب البريد الإلكتروني.
