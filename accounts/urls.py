from django.urls import path
from django.contrib.auth.views import LogoutView
from .views import PortalLoginView

urlpatterns = [

    path('login/', PortalLoginView.as_view(), name='login'),

    path('logout/', LogoutView.as_view(template_name='accounts/logout.html'), name='logout'),
]
