from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.views.generic import View, UpdateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from django.utils.translation import gettext as _
from django.views.generic.base import TemplateResponseMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from .models import UserPreference
from core.models import ObjectChange
from core.tables import ObjectChangeTable
from itambox.utils import get_paginate_count
from itambox.views.generic import BaseHTMXView
from django_tables2 import SingleTableView, RequestConfig
from .forms import UserProfileForm, UserPreferencesForm

User = get_user_model()

# User Account Views
class UserProfileView(LoginRequiredMixin, BaseHTMXView, UpdateView):
    model = User
    form_class = UserProfileForm
    template_name = 'users/profile.html'
    success_url = reverse_lazy('users:user_profile')

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, _("Profile updated successfully."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'profile'
        context['user'] = self.request.user
        from organization.models import TenantMembership
        context['user_memberships'] = TenantMembership.objects.filter(user=self.request.user).select_related('tenant', 'role')
        activity_qs = ObjectChange.objects.filter(user=self.request.user)[:15]
        activity_table = ObjectChangeTable(activity_qs, request=self.request)
        activity_table.configure(self.request, paginate=False)
        context['activity_table'] = activity_table
        context['title'] = _("User Profile")
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), _('Dashboard')),
            (None, context['title'])
        ]
        context['page_pretitle'] = _("User Account") # Add pretitle for wrapper
        return context

    # render_to_response handled by BaseHTMXView

class UserPasswordView(LoginRequiredMixin, BaseHTMXView, DjangoPasswordChangeView):
    template_name = 'users/password.html'
    success_url = reverse_lazy('users:user_profile')

    def form_valid(self, form):
        messages.success(self.request, _("Password changed successfully."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'password'
        context['user'] = self.request.user
        context['title'] = _("Change Password")
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), _('Dashboard')),
            (reverse_lazy('users:user_profile'), _('User Profile')),
            (None, context['title'])
        ]
        context['page_pretitle'] = _("User Account") # Add pretitle for wrapper
        return context

    # render_to_response handled by BaseHTMXView

class UserPreferencesView(LoginRequiredMixin, BaseHTMXView, TemplateResponseMixin, View):
    form_class = UserPreferencesForm
    template_name = 'users/preferences.html'

    def get_context_data(self, request, **kwargs):
        context = kwargs
        context['active_tab'] = 'preferences'
        context['user'] = request.user
        context['title'] = _("Preferences")
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), _('Dashboard')),
            (reverse_lazy('users:user_profile'), _('User Profile')),
            (None, context['title'])
        ]
        context['page_pretitle'] = _("User Account")
        # Ensure form is in context if not already passed (e.g., for initial GET)
        if 'form' not in context:
             context['form'] = self.form_class(user=request.user)
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(request)
        # BaseHTMXView will call TemplateResponseMixin.render_to_response via super()
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Preferences saved successfully."))
            response = redirect('users:user_preferences')
            # Apply the chosen interface language app-wide via the standard
            # language cookie (read by Django's LocaleMiddleware on every request).
            from django.conf import settings
            from django.utils import translation
            language = form.cleaned_data.get('language')
            if language and language in dict(settings.LANGUAGES):
                translation.activate(language)
                response.set_cookie(
                    settings.LANGUAGE_COOKIE_NAME,
                    language,
                    max_age=settings.LANGUAGE_COOKIE_AGE,
                    path=settings.LANGUAGE_COOKIE_PATH,
                    domain=settings.LANGUAGE_COOKIE_DOMAIN,
                    secure=settings.LANGUAGE_COOKIE_SECURE,
                    httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
                    samesite=settings.LANGUAGE_COOKIE_SAMESITE,
                )
            return response
        else:
            messages.error(request, _("There was an error saving your preferences."))
        # Pass invalid form back to context
        context = self.get_context_data(request, form=form)
        # BaseHTMXView will call TemplateResponseMixin.render_to_response via super()
        return self.render_to_response(context)

    # REMOVED custom render_to_response method


# Dummy Views for other tabs
class UserGenericTabView(LoginRequiredMixin, BaseHTMXView, TemplateView):
    template_name = 'users/dummy_tab.html'
    active_tab = ''
    tab_title = _('User Tab') # Add a default title

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.active_tab
        context['user'] = self.request.user # Ensure user is in context

        # --- Add Breadcrumbs & Title for BaseHTMXView --- 
        context['title'] = self.tab_title # Use the specific tab title
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), _('Dashboard')),
            (reverse_lazy('users:user_profile'), _('User Profile')), # Link back to profile
            (None, context['title'])
        ]
        # --- End Breadcrumbs & Title ---
        context['page_pretitle'] = _("User Account") # Add pretitle for wrapper
        return context

    # render_to_response handled by BaseHTMXView

class UserApiTokensView(UserGenericTabView):
    active_tab = 'api_tokens'
    tab_title = _('API Tokens')
    template_name = 'users/api_tokens.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import Token
        from .forms import TokenForm
        context['tokens'] = Token.objects.filter(user=self.request.user).order_by('-created')
        if 'form' not in context:
            context['form'] = TokenForm()
        if 'new_token_key' in self.request.session:
            context['new_token_key'] = self.request.session.pop('new_token_key')
        return context

    def post(self, request, *args, **kwargs):
        from .forms import TokenForm
        form = TokenForm(request.POST)
        if form.is_valid():
            token = form.save(commit=False)
            token.user = request.user
            from core.managers import get_current_tenant
            token.tenant = get_current_tenant()
            if form.cleaned_data.get('expires'):
                from django.utils import timezone
                import datetime
                expires_date = form.cleaned_data['expires']
                token.expires = timezone.make_aware(
                    datetime.datetime.combine(expires_date, datetime.time.max)
                )
            token.save()
            form.save_m2m()
            messages.success(
                request,
                _("API Token generated successfully! Make sure to copy your new personal access token now, as you won't be able to see it again: <code>{token_key}</code>").format(token_key=token.key)
            )
            request.session['new_token_key'] = token.key
            return redirect('users:user_api_tokens')
        
        context = self.get_context_data(form=form)
        return self.render_to_response(context)


class UserNotificationsView(UserGenericTabView):
    active_tab = 'notifications'
    tab_title = _('Notifications')
    template_name = 'users/notifications.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from core.models import Notification
        context['notifications'] = Notification.objects.filter(user=self.request.user).order_by('-created_at')
        return context


class MarkNotificationReadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from core.models import Notification
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        notification.is_read = True
        notification.save()
        return redirect('users:user_notifications')


class ViewNotificationView(LoginRequiredMixin, View):
    def get(self, request, pk):
        from core.models import Notification
        from django.db.models import Q
        notification = get_object_or_404(Notification, Q(user=request.user) | Q(user__isnull=True), pk=pk)
        
        if notification.user == request.user:
            notification.is_read = True
            notification.save()
            
        if notification.target_url:
            return redirect(notification.target_url)
        return redirect('users:user_notifications')


class MarkAllNotificationsReadView(LoginRequiredMixin, View):
    def post(self, request):
        from core.models import Notification
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return redirect('users:user_notifications')


class DeleteApiTokenView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from .models import Token
        token = get_object_or_404(Token, pk=pk, user=request.user)
        token.delete()
        messages.success(request, _("API Token has been revoked."))
        return redirect('users:user_api_tokens')



@login_required
def notification_poll(request):
    """HTMX polling endpoint returning notification dropdown content.
    
    Returns the notification bell dropdown HTML and an OOB badge update.
    Polled every 30 seconds by the topbar notification bell.
    Returns 204 No Content for non-HTMX requests.
    """
    if not getattr(request, 'htmx', False):
        return HttpResponse(status=204)

    from core.models import Notification
    unread_qs = Notification.objects.filter(user=request.user, is_read=False)
    context = {
        'unread_notifications_count': unread_qs.count(),
        'recent_unread_notifications': unread_qs.order_by('-created_at')[:5],
    }
    return render(request, 'htmx/notification_dropdown.html', context)


class UserBookmarksView(UserGenericTabView):
    active_tab = 'bookmarks'
    tab_title = 'Bookmarks'
    template_name = 'users/bookmarks.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from extras.models import Bookmark
        from extras.utils import resolve_generic_items
        user_bookmarks = list(Bookmark.objects.filter(user=self.request.user).select_related('model'))
        context['bookmarked_items'] = resolve_generic_items(user_bookmarks)
        context['bookmarked_count'] = len(context['bookmarked_items'])
        return context


class UserSubscriptionsView(UserGenericTabView):
    active_tab = 'watching'
    tab_title = 'Watching'
    template_name = 'users/watching.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from extras.models import ObjectWatch
        from extras.utils import resolve_generic_items
        user_watches = list(ObjectWatch.objects.filter(user=self.request.user).select_related('model'))
        context['watched_items'] = resolve_generic_items(user_watches, toggle_url_name='users:watch_toggle')
        context['watched_count'] = len(context['watched_items'])
        return context


class BookmarkToggleView(LoginRequiredMixin, View):
    """
    Toggle a user bookmark for a generic object (used via HTMX).
    Returns the updated HTMX button state or an empty response on list page delete.
    """
    def post(self, request, content_type_id, object_id):
        import json
        from django.http import Http404
        from extras.models import Bookmark
        from django.contrib.contenttypes.models import ContentType
        from itambox.registry import registry

        content_type = get_object_or_404(ContentType, id=content_type_id)
        model_class = content_type.model_class()
        if model_class is None or not registry.model_has_feature(model_class, 'bookmarkable'):
            raise Http404

        target_obj = get_object_or_404(model_class, id=object_id)

        app_label = content_type.app_label
        model_name = content_type.model
        if not request.user.has_perm(f'{app_label}.view_{model_name}', target_obj):
            raise Http404

        bookmark_qs = Bookmark.objects.filter(
            user=request.user,
            model=content_type,
            object_id=object_id
        )

        if bookmark_qs.exists():
            bookmark_qs.delete()
            is_bookmarked = False
        else:
            Bookmark.objects.create(
                user=request.user,
                model=content_type,
                object_id=object_id
            )
            is_bookmarked = True

        if getattr(request, 'htmx', False):
            # ?context=list → list-page row; omitted/other → detail-page button
            if request.GET.get('context') == 'list':
                msg = _("Unsubscribed from {name}.").format(name=str(target_obj)) if not is_bookmarked else _("Bookmarked {name}.").format(name=str(target_obj))
                response = HttpResponse("")
                response['HX-Trigger'] = json.dumps({"showMessage": {"message": msg, "level": "success"}})
                return response

            from django.middleware.csrf import get_token
            csrf_token = get_token(request)
            btn_class = 'btn-soft-warning' if is_bookmarked else 'btn-ghost-secondary'
            star_icon = 'mdi-star' if is_bookmarked else 'mdi-star-outline'
            title = 'Remove Bookmark' if is_bookmarked else 'Bookmark'
            button_html = (
                f'<button type="button" class="btn btn-icon {btn_class}"'
                f' hx-post="{reverse("users:bookmark_toggle", kwargs={"content_type_id": content_type_id, "object_id": object_id})}"'
                f' hx-headers=\'{{"X-CSRFToken": "{csrf_token}"}}\''
                f' hx-target="this" hx-swap="outerHTML" title="{title}">'
                f'<i class="mdi {star_icon}"></i></button>'
            )
            msg = _("Bookmarked {name}.").format(name=str(target_obj)) if is_bookmarked else _("Bookmark removed from {name}.").format(name=str(target_obj))
            response = HttpResponse(button_html)
            response['HX-Trigger'] = json.dumps({"showMessage": {"message": msg, "level": "success"}})
            return response

        return redirect(target_obj.get_absolute_url())


class WatchToggleView(LoginRequiredMixin, View):
    """Toggle an ObjectWatch for a generic object (used via HTMX)."""
    def post(self, request, content_type_id, object_id):
        import json
        from django.http import Http404
        from extras.models import ObjectWatch
        from django.contrib.contenttypes.models import ContentType
        from itambox.registry import registry

        content_type = get_object_or_404(ContentType, id=content_type_id)
        model_class = content_type.model_class()
        if model_class is None or not registry.model_has_feature(model_class, 'watchable'):
            raise Http404

        target_obj = get_object_or_404(model_class, id=object_id)

        app_label = content_type.app_label
        model_name = content_type.model
        if not request.user.has_perm(f'{app_label}.view_{model_name}', target_obj):
            raise Http404

        watch_qs = ObjectWatch.objects.filter(
            user=request.user,
            model=content_type,
            object_id=object_id
        )

        if watch_qs.exists():
            watch_qs.delete()
            is_watched = False
        else:
            ObjectWatch.objects.create(
                user=request.user,
                model=content_type,
                object_id=object_id
            )
            is_watched = True

        if getattr(request, 'htmx', False):
            # ?context=list → list-page row; omitted/other → detail-page button
            if request.GET.get('context') == 'list':
                msg = _("Unwatched {name}.").format(name=str(target_obj)) if not is_watched else _("Now watching {name}.").format(name=str(target_obj))
                response = HttpResponse("")
                response['HX-Trigger'] = json.dumps({"showMessage": {"message": msg, "level": "success"}})
                return response

            from django.middleware.csrf import get_token
            csrf_token = get_token(request)
            btn_class = 'btn-soft-info' if is_watched else 'btn-ghost-secondary'
            bell_icon = 'mdi-bell' if is_watched else 'mdi-bell-outline'
            title = 'Stop Watching' if is_watched else 'Watch (notify me on changes)'
            button_html = (
                f'<button type="button" class="btn btn-icon {btn_class}"'
                f' hx-post="{reverse("users:watch_toggle", kwargs={"content_type_id": content_type_id, "object_id": object_id})}"'
                f' hx-headers=\'{{"X-CSRFToken": "{csrf_token}"}}\''
                f' hx-target="this" hx-swap="outerHTML" title="{title}">'
                f'<i class="mdi {bell_icon}"></i></button>'
            )
            msg = _("Now watching {name}.").format(name=str(target_obj)) if is_watched else _("Unwatched {name}.").format(name=str(target_obj))
            response = HttpResponse(button_html)
            response['HX-Trigger'] = json.dumps({"showMessage": {"message": msg, "level": "success"}})
            return response

        return redirect(target_obj.get_absolute_url())


# User Management Views (Frontend Admin)
from itambox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
from .tables import UserTable
from .filters import UserFilterSet
from .forms import UserFilterForm, UserForm

class UserListView(ObjectListView):
    queryset = User.objects.all()
    filterset = UserFilterSet
    filterset_form = UserFilterForm
    table = UserTable
    action_buttons = ('add',)


class UserDetailView(ObjectDetailView):
    queryset = User.objects.prefetch_related('memberships__tenant', 'memberships__role')
    template_name = 'users/user_detail.html'

    def has_permission(self):
        return self.request.user.has_perms(self.get_permission_required())


class UserEditView(ObjectEditView):
    queryset = User.objects.all()
    model = User
    model_form = UserForm
    template_name = 'generic/object_edit.html'

    def has_permission(self):
        return self.request.user.has_perms(self.get_permission_required())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs


class UserDeleteView(ObjectDeleteView):
    queryset = User.objects.all()
    model = User
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('users:user_list')

    def has_permission(self):
        return self.request.user.has_perms(self.get_permission_required())

    def post(self, request, *args, **kwargs):
        user_to_delete = self.get_object()
        if user_to_delete == request.user:
            messages.error(request, _("You cannot delete your own user account."))
            return redirect(reverse('users:user_detail', kwargs={'pk': user_to_delete.pk}))
        return super().post(request, *args, **kwargs)
