"""
URL configuration for EnjazIMS project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.core.views import service_worker, pwa_manifest

urlpatterns = [
    # PWA
    path('sw.js', service_worker, name='service_worker'),
    path('manifest.json', pwa_manifest, name='pwa_manifest'),

    # Admin
    path('admin/', admin.site.urls),
    
    # Apps
    path('', include('apps.core.urls')),
    path('accounts/', include('apps.accounts.urls')),
    path('customers/', include('apps.customers.urls')),
    path('suppliers/', include('apps.suppliers.urls')),
    path('agents/', include('apps.agents.urls')),
    path('stocks/', include('apps.stocks.urls')),
    path('items/', include('apps.items.urls')),
    path('purchases/', include('apps.purchases.urls')),
    path('sales/', include('apps.sales.urls')),
    path('reports/', include('apps.sales.reports_urls')),
    path('treasury/', include('apps.treasury.urls')),
    path('expenses/', include('apps.expenses.urls')),
    path('notifications/', include('apps.notifications.urls')),
    path('portal/', include('apps.portal.urls')),
    path('ai/', include('apps.ai.urls')),
    path('store/', include('apps.store.urls')),
    path('employees/', include('apps.employees.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else settings.STATIC_ROOT)
