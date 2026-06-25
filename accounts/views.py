from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy


class PortalLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy("home")
