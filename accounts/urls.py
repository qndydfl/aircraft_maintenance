from django.urls import path
from django.contrib.auth.views import LoginView
from django.contrib.auth.views import LogoutView

urlpatterns = [

    path('login/', LoginView.as_view(template_name='accounts/login.html'), name='login'),

    path('logout/', LogoutView.as_view(template_name='accounts/logout.html'), name='logout'),
]