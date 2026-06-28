from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.views.generic import View, UpdateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
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
        from organization.models import Membership
        context['user_memberships'] = Membership.objects.filter(user=self.request.user).select_related('tenant').prefetch_related('roles')
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
        # Only the recipient may open a notification by pk. The previous
        # Q(user__isnull=True) clause let ANY authenticated user (any tenant) open a global
        # broadcast row by pk and follow its target_url — a cross-tenant info leak. Tenant
        # EventRule notifications now fan out per-user instead of creating user=None rows.
        notification = get_object_or_404(Notification, pk=pk, user=request.user)

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
    tab_title = _lazy('Bookmarks')
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
    tab_title = _lazy('Watching')
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
            title = _('Remove Bookmark') if is_bookmarked else _('Bookmark')
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
            title = _('Stop Watching') if is_watched else _('Watch (notify me on changes)')
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
from itambox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, safe_return_url
from django.http import HttpResponseRedirect
from .tables import UserTable
from .filters import UserFilterSet
from .forms import UserFilterForm, UserForm, UserBulkEditForm

class UserListView(ObjectListView):
    queryset = User.objects.all()
    filterset = UserFilterSet
    filterset_form = UserFilterForm
    table = UserTable
    action_buttons = ('add',)

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_superuser:
            return qs
        from core.managers import get_current_tenant
        active_tenant = get_current_tenant()
        if active_tenant is None:
            return qs.none()
        return qs.filter(memberships__tenant=active_tenant).distinct()


class UserBulkEditView(ObjectBulkEditView):
    queryset = User.objects.all()
    form_class = UserBulkEditForm
    table = UserTable
    template_name = 'generic/object_bulk_edit.html'

    def _get_bulk_edit_form(self, data=None, model=None):
        return self.form_class(data, model=model, request_user=self.request.user)

    def _get_queryset(self, pks):
        qs = super()._get_queryset(pks)
        if self.request.user.is_superuser:
            return qs
        from core.managers import get_current_tenant
        active_tenant = get_current_tenant()
        if active_tenant is None:
            return qs.none()
        return qs.filter(memberships__tenant=active_tenant).distinct()

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        model = self._get_model()
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('dashboard'),
        )
        raw_selected_fields = request.POST.getlist('_selected_fields')
        selected_fields = [f for f in raw_selected_fields if f not in ('add_tags', 'remove_tags')]

        if not pks:
            messages.warning(request, _("No %(objects)s were selected.") % {'objects': model._meta.verbose_name_plural})
            return HttpResponseRedirect(return_url)

        queryset = self._get_queryset(pks)

        if '_apply' in request.POST:
            form = self._get_bulk_edit_form(request.POST, model)
            if form.is_valid():
                # Self-lockout check
                if request.user in queryset:
                    is_active = form.cleaned_data.get('is_active')
                    is_superuser = form.cleaned_data.get('is_superuser')
                    is_staff = form.cleaned_data.get('is_staff')
                    can_login = form.cleaned_data.get('can_login')

                    if 'can_login' in selected_fields and can_login is False:
                        messages.error(request, _("You cannot revoke your own login ability in a bulk edit operation."))
                        context = self.get_context_data_compat(form, queryset, pks, return_url, selected_fields, model)
                        return self.render_to_response(context)

                    if 'is_active' in selected_fields and is_active is False:
                        messages.error(request, _("You cannot deactivate your own user account in a bulk edit operation."))
                        context = self.get_context_data_compat(form, queryset, pks, return_url, selected_fields, model)
                        return self.render_to_response(context)

                    if 'is_superuser' in selected_fields and is_superuser is False:
                        messages.error(request, _("You cannot revoke your own superuser status in a bulk edit operation."))
                        context = self.get_context_data_compat(form, queryset, pks, return_url, selected_fields, model)
                        return self.render_to_response(context)

                    if 'is_staff' in selected_fields and is_staff is False:
                        messages.error(request, _("You cannot revoke your own staff status in a bulk edit operation."))
                        context = self.get_context_data_compat(form, queryset, pks, return_url, selected_fields, model)
                        return self.render_to_response(context)

        return super().post(request, *args, **kwargs)

    def get_context_data_compat(self, form, queryset, pks, return_url, selected_fields, model):
        return {
            'form': form,
            'model': model,
            'model_name': f'{model._meta.app_label}.{model._meta.model_name}',
            'objects': queryset,
            'object_pks': pks,
            'return_url': return_url,
            'selected_fields': selected_fields,
            'verbose_name': model._meta.verbose_name,
            'verbose_name_plural': model._meta.verbose_name_plural,
            'title': _('Bulk Edit %(objects)s') % {'objects': str(model._meta.verbose_name_plural).title()},
            'breadcrumbs': [
                (reverse('dashboard'), _('Dashboard')),
                (return_url, str(model._meta.verbose_name_plural).title()),
                (None, _('Bulk Edit (%(count)s)') % {'count': len(pks)}),
            ],
        }


class UserDetailView(ObjectDetailView):
    queryset = User.objects.prefetch_related(
        'memberships__tenant', 'memberships__provider', 'memberships__roles',
    )
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


# --------------------------------------------------------------------------- UserGroup
# Relocated from organization/ — UserGroup is an identity-layer construct.
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import transaction
from django.db.models import Count, Prefetch
from itambox.views.generic import ObjectBulkDeleteView
from organization.models import Role, Membership
from .models import UserGroup
from .tables import UserGroupTable
from .filters import UserGroupFilterSet
from .forms import UserGroupForm, UserGroupFilterForm, UserGroupAssignUsersForm


def is_global_group_admin(user):
    """User groups are global and can grant cross-tenant access, so only global admins
    may manage them: superusers, provider staff holding ``can_manage_groups``, OR a user
    directly granted the legacy ``organization.manage_groups`` capability (single-company
    backward compat). Delegates to core.auth.provider.can_manage_user_groups."""
    from core.auth.provider import can_manage_user_groups
    return can_manage_user_groups(user)


class GlobalGroupAdminMixin(UserPassesTestMixin):
    """Restrict a UserGroup view to global admins (see is_global_group_admin).

    Group management is gated SOLELY on the global group-management capability — never on
    the per-model ``view/add/change_usergroup`` permissions. ``test_func`` enforces the
    capability; ``get_permission_required`` returns an empty set so the generic
    PermissionRequiredMixin in the MRO does not additionally require ``view_usergroup``."""
    def test_func(self):
        return is_global_group_admin(self.request.user)

    def get_permission_required(self):
        return ()


class UserGroupListView(GlobalGroupAdminMixin, ObjectListView):
    queryset = UserGroup.objects.annotate(
        member_count=Count('members', distinct=True),
        role_count=Count('roles', distinct=True),
    )
    filterset = UserGroupFilterSet
    filterset_form = UserGroupFilterForm
    table = UserGroupTable
    action_buttons = ('add',)


class UserGroupDetailView(GlobalGroupAdminMixin, ObjectDetailView):
    queryset = UserGroup.objects.prefetch_related(
        Prefetch('roles', queryset=Role.objects.order_by('scope', 'name')),
        'members',
    ).annotate(
        member_count=Count('members', distinct=True),
    )
    template_name = 'users/usergroups/usergroup_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.get_object()

        # Build union of permissions across all attached roles (for detail display).
        all_perms = set()
        for role in group.roles.all():
            all_perms.update(role.permissions or [])
        context['effective_permissions'] = sorted(all_perms)

        context['members'] = group.members.all().order_by('username')
        context['roles'] = group.roles.all()
        context['member_count'] = getattr(group, 'member_count', 0) or 0
        return context


class UserGroupEditView(GlobalGroupAdminMixin, ObjectEditView):
    queryset = UserGroup.objects.all()
    model = UserGroup
    model_form = UserGroupForm
    template_name = 'generic/object_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['tenant'] = getattr(self.request, 'active_tenant', None)
        return kwargs


class UserGroupDeleteView(GlobalGroupAdminMixin, ObjectDeleteView):
    queryset = UserGroup.objects.all()
    model = UserGroup
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('users:usergroup_list')


class UserGroupBulkDeleteView(GlobalGroupAdminMixin, ObjectBulkDeleteView):
    queryset = UserGroup.objects.all()

    def post(self, request, *args, **kwargs):
        from django.http import HttpResponseRedirect

        pks = request.POST.getlist('pk')
        model = self._get_model()
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('users:usergroup_list'),
        )

        if not pks:
            messages.warning(request, _("No user groups were selected."))
            return HttpResponseRedirect(return_url)

        queryset = self._get_queryset(pks)
        objects_to_delete = list(queryset)

        if not objects_to_delete:
            messages.warning(request, _("No valid user groups selected for deletion."))
            return HttpResponseRedirect(return_url)

        if '_confirm' in request.POST:
            deleted_count = 0
            with transaction.atomic():
                for obj in objects_to_delete:
                    obj.delete()
                    deleted_count += 1
            messages.success(
                request,
                _("Successfully deleted %(count)d user group(s).") % {'count': deleted_count},
            )
            return HttpResponseRedirect(return_url)
        else:
            context = {
                'model': model,
                'model_name': f'{model._meta.app_label}.{model._meta.model_name}',
                'model_verbose_name': model._meta.verbose_name,
                'model_verbose_name_plural': model._meta.verbose_name_plural,
                'objects': objects_to_delete,
                'object_pks': pks,
                'return_url': return_url,
                'title': _('Confirm Bulk Deletion'),
                'breadcrumbs': [
                    (reverse('dashboard'), _('Dashboard')),
                    (return_url, _('User Groups')),
                    (None, _('Delete (%(count)d)') % {'count': len(objects_to_delete)}),
                ],
            }
            return self.render_to_response(context)


class UserGroupAssignUsersView(GlobalGroupAdminMixin, LoginRequiredMixin, View):
    """Add one or more users to a (global) UserGroup's members (idempotent).

    Groups are global, so any user may be a member; management is restricted to
    global admins (GlobalGroupAdminMixin).
    """
    template_name = 'users/usergroups/usergroup_assign_users.html'

    def _get_group(self, pk):
        return get_object_or_404(UserGroup, pk=pk)

    def get(self, request, pk, *args, **kwargs):
        group = self._get_group(pk)
        form = UserGroupAssignUsersForm()
        return render(request, self.template_name, {'group': group, 'form': form})

    def post(self, request, pk, *args, **kwargs):
        group = self._get_group(pk)
        form = UserGroupAssignUsersForm(request.POST)
        if form.is_valid():
            users = form.cleaned_data['users']
            added = 0
            already_member = 0
            with transaction.atomic():
                for user in users:
                    if group.members.filter(pk=user.pk).exists():
                        already_member += 1
                    else:
                        group.members.add(user)
                        added += 1
            messages.success(
                request,
                _("User group '%(group)s': %(added)d added, %(already)d already member.") % {
                    'group': group.name,
                    'added': added,
                    'already': already_member,
                },
            )
            return redirect(reverse('users:usergroup_detail', kwargs={'pk': group.pk}))

        return render(request, self.template_name, {'group': group, 'form': form})
