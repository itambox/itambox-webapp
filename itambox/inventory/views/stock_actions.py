from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

from itambox.views.generic.authorization import SecuredObjectActionMixin
from itambox.views.generic.htmx_responses import is_htmx_request, success_response


class StockActionView(SecuredObjectActionMixin, LoginRequiredMixin, PermissionRequiredMixin, View):
    """Secure tenant-scoped foundation for inventory stock actions."""

    strict_pk_required = True


class StockAdjustView(StockActionView):
    """Adjust one stock pool and return the replacement quantity control."""

    def get_locked_object(self):
        # Permission dispatch already resolved this pool through the shared,
        # tenant-scoped get_object(). Re-select that same row inside the atomic
        # block so concurrent +/- requests cannot lose an update.
        stock = self.get_object()
        return get_object_or_404(
            self.get_queryset().select_for_update(),
            pk=stock.pk,
        )

    def apply_action(self, stock, action):
        if action == "increment":
            stock.qty += 1
            stock.save()
        elif action == "decrement" and stock.qty > 0:
            stock.qty -= 1
            stock.save()

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            stock = self.get_locked_object()
            self.apply_action(stock, request.GET.get("action"))
        return HttpResponse(self.render_control(stock))

    def render_control(self, stock):
        adjust_url = reverse(
            f"{stock._meta.app_label}:{stock._meta.model_name}_adjust",
            kwargs={"pk": stock.pk},
        )
        return format_html(
            '<div class="d-flex align-items-center justify-content-start">'
            '  <button class="stock-adjust-button btn btn-sm btn-icon btn-outline-secondary me-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div">'
            '    <i class="stock-adjust-icon mdi mdi-minus"></i>'
            "  </button>"
            '  <span class="stock-adjust-quantity badge bg-blue-lt text-blue font-weight-bold px-2 py-1">{}</span>'
            '  <button class="stock-adjust-button btn btn-sm btn-icon btn-outline-secondary ms-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div">'
            '    <i class="stock-adjust-icon mdi mdi-plus"></i>'
            "  </button>"
            "</div>",
            f"{adjust_url}?action=decrement",
            stock.qty,
            f"{adjust_url}?action=increment",
        )


class StockCreateModalView(StockActionView):
    """Create a stock pool for one tenant-scoped inventory catalogue item."""

    modal_form = None
    template_name = "generic/includes/add_stock_modal.html"

    def get_initial(self):
        initial = {}
        location_id = self.request.GET.get("location")
        if location_id:
            initial["location"] = location_id
        return initial

    def get_post_url(self, item):
        return reverse(
            f"{item._meta.app_label}:{item._meta.model_name}_add_stock",
            kwargs={"pk": item.pk},
        )

    def get_context(self, item, form):
        return {
            "object": item,
            "form": form,
            "post_url": self.get_post_url(item),
        }

    def get(self, request, *args, **kwargs):
        item = self.get_object()
        form = self.modal_form(initial=self.get_initial())
        return render(request, self.template_name, self.get_context(item, form))

    def post(self, request, *args, **kwargs):
        item = self.get_object()
        form = self.modal_form(request.POST)
        if form.is_valid():
            stock = form.save(commit=False)
            setattr(stock, item._meta.model_name, item)
            stock.save()
            if is_htmx_request(request):
                return success_response(_("Added stock pool for %(location)s.") % {"location": stock.location})
            return redirect(item.get_absolute_url())

        return render(request, self.template_name, self.get_context(item, form))
