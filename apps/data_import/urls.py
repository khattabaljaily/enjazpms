from django.urls import path
from . import entity_importer, views

app_name = 'data_import'

urlpatterns = [
    path('', views.hub, name='hub'),

    path('products/', views.product_import_page, name='product_page'),
    path('products/template/', views.product_template_download, name='product_template'),
    path('products/analyze/', views.product_import_analyze, name='product_analyze'),
    path('products/suggest-categories/', views.product_suggest_categories, name='product_suggest_categories'),
    path('products/commit/', views.product_import_commit, name='product_commit'),
    path('products/errors-file/', views.product_import_errors_file, name='product_errors_file'),
]

# العملاء والموردون والمناديب والتصنيفات: نفس مسار الاستيراد الذكي (رفع ← معاينة ← تأكيد)
for _entity, _views in views.ENTITY_VIEWS.items():
    _slug = entity_importer.ENTITIES[_entity]['slug']
    urlpatterns += [
        path(f'{_entity}/', _views['page'], name=f'{_slug}_page'),
        path(f'{_entity}/template/', _views['template'], name=f'{_slug}_template'),
        path(f'{_entity}/analyze/', _views['analyze'], name=f'{_slug}_analyze'),
        path(f'{_entity}/commit/', _views['commit'], name=f'{_slug}_commit'),
        path(f'{_entity}/errors-file/', _views['errors_file'], name=f'{_slug}_errors_file'),
    ]
