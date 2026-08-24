from django.urls import path
from . import views

app_name = 'store'

urlpatterns = [
    # ── Tenant management (must come before <slug> patterns) ──
    path('manage/',                         views.manage_settings,      name='manage_settings'),
    path('manage/orders/',                  views.manage_orders,         name='manage_orders'),
    path('manage/orders/<int:pk>/',         views.manage_order_detail,   name='manage_order_detail'),
    path('manage/orders/<int:pk>/approve/', views.manage_order_approve,  name='manage_order_approve'),
    path('manage/orders/<int:pk>/reject/',  views.manage_order_reject,   name='manage_order_reject'),

    # ── Public storefront ──────────────────────────────────
    path('<slug:slug>/',                views.storefront,        name='storefront'),
    path('<slug:slug>/price-list/',     views.price_list,        name='price_list'),
    path('<slug:slug>/cart/',           views.cart_view,         name='cart'),
    path('<slug:slug>/cart/add/',       views.cart_add_view,     name='cart_add'),
    path('<slug:slug>/cart/remove/',    views.cart_remove_view,  name='cart_remove'),
    path('<slug:slug>/cart/update/',    views.cart_update_view,  name='cart_update'),
    path('<slug:slug>/checkout/',       views.checkout_view,     name='checkout'),
    path('<slug:slug>/order/<uuid:token>/', views.order_confirm_view, name='order_confirm'),
]
