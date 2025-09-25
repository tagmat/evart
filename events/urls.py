from django.urls import path
from . import views

urlpatterns = [
    path('generate-yaml/<int:service_id>/', views.generate_full_yaml, name='generate_yaml'),
    path('import-yaml/', views.import_yaml, name='import_yaml'),
    path('import-yaml-page/', views.import_yaml_page, name='import_yaml_page'),
]
