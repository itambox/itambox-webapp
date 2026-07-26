"""Shared HTMX response helpers.

Action views answer HTMX callers with ``204 No Content`` plus an ``HX-Trigger``
header carrying a JSON payload of client-side events. Four separate copies of
that payload-building code had drifted apart across the service views and the
restore/purge views; this module is the single implementation.

The client contract (see ``static/src/state.ts``):

``closeModalEvent``
    dismiss the modal the action was submitted from.
``tableRefreshRequired`` (or a view-specific ``hx_trigger``)
    re-fetch the list/table the action mutated.
``showMessage``
    render a toast — ``{"message": ..., "level": "success" | "danger"}``.
"""

import json

from django.http import HttpResponse

#: Dismiss the modal the action was submitted from.
HX_CLOSE_MODAL = "closeModalEvent"
#: Default "the data behind this table changed" refresh trigger.
HX_TABLE_REFRESH = "tableRefreshRequired"
#: Toast event name.
HX_SHOW_MESSAGE = "showMessage"


def is_htmx_request(request):
    """True when the caller is HTMX.

    ``HtmxMiddleware`` is installed unconditionally, so ``request.htmx`` is
    authoritative for real requests; the raw header is also honoured so that
    views remain testable with a bare ``RequestFactory``.
    """
    return bool(getattr(request, "htmx", False) or request.headers.get("HX-Request"))


def trigger_response(triggers, status=204):
    """An empty response whose ``HX-Trigger`` header carries ``triggers`` as JSON."""
    response = HttpResponse(status=status)
    response["HX-Trigger"] = json.dumps(triggers)
    return response


def success_response(message, trigger=HX_TABLE_REFRESH, close_modal=True):
    """The standard success payload: close the modal, refresh, toast.

    ``close_modal=False`` serves the page-level actions (restore/purge) that are
    not submitted from a modal. ``message`` is coerced with ``str()`` so lazy
    translation proxies survive JSON serialisation.
    """
    triggers = {}
    if close_modal:
        triggers[HX_CLOSE_MODAL] = None
    triggers[trigger] = None
    triggers[HX_SHOW_MESSAGE] = {"message": str(message), "level": "success"}
    return trigger_response(triggers)


def error_response(message):
    """A danger toast and nothing else.

    Deliberately carries no refresh trigger: the action failed, so there is
    nothing new to fetch.
    """
    return trigger_response({HX_SHOW_MESSAGE: {"message": str(message), "level": "danger"}})
