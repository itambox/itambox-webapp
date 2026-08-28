import base64
import io
import logging
import zipfile
from collections.abc import Iterator, Sequence
from math import isfinite
from pathlib import Path
from typing import TypeVar

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.utils.html import escape, format_html
from django.utils.translation import gettext_lazy as _

from assets.models import Asset
from core.html_sanitizer import sanitize_label_html_for_pdf
from core.models import Job, Notification
from core.pdf_renderer import html_to_pdf_bytes, pdf_safe_link_callback
from core.tasks.context import TaskContext
from core.tasks.utils import TaskResult, TaskStatus, classify_task_error, reverse_job_detail
from extras.models import FileAttachment, LabelTemplate

logger = logging.getLogger(__name__)
_LABEL_PRINT_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "src" / "styles" / "_label-print.scss"

# The element type ``chunk_list`` pages over; it never inspects an element.
_ChunkItem = TypeVar("_ChunkItem")


def _label_print_css() -> str:
    """Load the authored label CSS for the self-contained PDF document."""
    return _LABEL_PRINT_CSS_PATH.read_text(encoding="utf-8")


def _try_create_notification(user, *, subject, message, level, target_url=None, log_extra=None, job=None):
    """Deliver a task notification best-effort (failure isolation).

    Notification delivery must never reverse an already-durable task outcome
    (completed job + persisted attachment) nor mask the original task error,
    so creation failures are logged with safe metadata only and swallowed.
    """
    try:
        Notification.objects.create(user=user, subject=subject, message=message, level=level, target_url=target_url)
    # broad except: availability-tradeoff: notification delivery is best-effort and must
    # never reverse a durable task outcome (completed job + persisted attachment) nor mask
    # the original task error, so creation failures are logged with safe metadata only.
    except Exception as exc:
        logger.error(
            "Task notification delivery failed",
            extra={**(log_extra or {}), "phase": "notification", "exception_type": type(exc).__name__},
        )
        if job is not None:
            try:
                job.append_log("Notification delivery failed (phase=notification).")
            # broad except: boundary-isolation: a failing job-log append must not cascade
            # while handling a notification failure
            except Exception as log_exc:
                logger.error(
                    "Job log append failed while handling notification failure",
                    extra={**(log_extra or {}), "phase": "notification", "exception_type": type(log_exc).__name__},
                )


def _cleanup_partial_attachment(job, attachment, log_extra):
    """Best-effort removal of a partial attachment after a failed persistence step.

    A retry must not accumulate orphaned FileAttachment rows or stored files:
    once a failure reached the task boundary, the job is not durably completed
    (success notifications are best-effort), so any attachment created by this
    run is removed, including its stored file (Django does not delete FileField
    files on model deletion). Cleanup failures are logged with safe metadata
    only.
    """
    if attachment is None:
        return
    try:
        attachment.file.delete(save=False)
        attachment.delete()
    # broad except: availability-tradeoff: attachment cleanup is best-effort; a cleanup
    # failure must not cascade into the already-recorded task boundary outcome
    except Exception as exc:
        logger.error(
            "Attachment cleanup failed",
            extra={**log_extra, "phase": "attachment_cleanup", "exception_type": type(exc).__name__},
        )


def _resolve_label_task_assets(asset_model, asset_pks, scope_tenant_ids, user):
    assets = list(
        asset_model._base_manager.filter(
            pk__in=asset_pks,
            tenant_id__in=scope_tenant_ids,
        )
    )
    expected_asset_count = len({int(pk) for pk in asset_pks})
    if len(assets) != expected_asset_count:
        return assets, "inaccessible"
    if (
        user is not None
        and not user.is_superuser
        and not all(user.has_perm("assets.view_asset", obj=asset) for asset in assets)
    ):
        return assets, "inaccessible"
    if not assets:
        return assets, "empty"
    return assets, None


def _finish_label_asset_resolution(job, resolution):
    if resolution == "inaccessible":
        job.append_log("Some selected assets are no longer accessible.")
        job.mark_failed("[terminal] labels.assets_not_accessible")
        return TaskResult(TaskStatus.TERMINAL, "labels.assets_not_accessible", user_visible=True)
    job.append_log("No matching assets found to print.")
    job.mark_completed(result={"status": "no_assets"})
    return TaskResult(TaskStatus.SKIPPED, "labels.no_assets", user_visible=True)


def _safe_label_measurement(value: object, default: float) -> str:
    """Return a bounded, CSS-safe inch measurement for a label dimension."""
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        numeric = default
    if not isfinite(numeric) or not 0.1 <= numeric <= 100:
        numeric = default
    return f"{numeric:.3f}".rstrip("0").rstrip(".")


def generate_single_label_graphic(asset: object, label_format: str) -> bytes:
    """
    Renders QR code or Barcode PNG bytes for the given asset.
    """
    buffer = io.BytesIO()

    if label_format == "qr":
        # inline import: heavy-import: load barcode/QR renderers only when generating graphics
        import segno

        # Generate clean QR code
        qr = segno.make_qr(f"itambox://asset/{asset.pk}")
        qr.save(buffer, kind="png", scale=10)
    else:
        # inline import: heavy-import: load barcode/QR renderers only when generating graphics
        import barcode

        # inline import: heavy-import: load barcode/QR renderers only when generating graphics
        from barcode.writer import ImageWriter

        # Generate barcode
        CODING = barcode.get_barcode_class("code128")
        # Clean text
        code = CODING(asset.asset_tag or str(asset.pk), writer=ImageWriter())
        code.write(buffer)

    return buffer.getvalue()


def _finalize_label_zip(job, user, zip_buffer: io.BytesIO, rendered: int, total: int) -> TaskResult:
    if rendered == 0:
        job.mark_failed("[terminal] labels.no_labels_rendered")
        return TaskResult(TaskStatus.TERMINAL, "labels.no_labels_rendered", user_visible=True)

    zip_buffer.seek(0)
    ct = ContentType.objects.get_for_model(Job)
    attachment = FileAttachment.objects.create(
        model=ct, object_id=job.pk, name=f"labels_batch_{job.pk}.zip", mime_type="application/zip"
    )
    attachment.file.save(f"labels_batch_{job.pk}.zip", ContentFile(zip_buffer.getvalue()))
    attachment.save()

    job.append_log("ZIP package generated and saved successfully.")
    job.mark_completed(result={"file_name": attachment.name, "download_url": attachment.get_download_url()})
    _try_create_notification(
        user,
        subject=_("Label Generation Complete"),
        message=_("Successfully generated label batch zip for %(count)s asset(s). Click to download.")
        % {"count": rendered},
        level=Notification.LEVEL_SUCCESS,
        target_url=attachment.get_download_url(),
        log_extra={"job_id": job.pk},
        job=job,
    )
    return TaskResult(
        TaskStatus.PARTIAL if rendered < total else TaskStatus.SUCCESS,
        "labels.zip_partial" if rendered < total else "labels.zip_completed",
        {"rendered": rendered, "failed": total - rendered},
        user_visible=True,
    )


def _render_batch_zip(job, assets, label_format, log_extra):
    """Render one PNG per asset into a ZIP buffer; one failure never aborts."""
    zip_buffer = io.BytesIO()
    rendered = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for asset in assets:
            try:
                img_data = generate_single_label_graphic(asset, label_format)
                filename = f"label_{asset.asset_tag}_{label_format}.png"
                zip_file.writestr(filename, img_data)
                rendered += 1
                job.append_log(f" - Rendered label for asset PK {asset.pk}.")
            # broad except: boundary-isolation: one label failure must not abort the requested batch
            except Exception as ex:
                job.append_log(f" - Asset PK {asset.pk} failed [labels.item_failed].")
                logger.warning(
                    "Label rendering failed",
                    extra={**log_extra, "object_id": asset.pk, "exception_type": type(ex).__name__},
                )
    return zip_buffer, rendered


def generate_label_batch_task(
    job_id: int,
    asset_pks: Sequence[int | str],
    label_format: str,
    user_id: int | None,
    tenant_id: int | None = None,
) -> TaskResult:
    """
    Asynchronously generates QR-codes/barcodes for selected assets,
    packages them into a ZIP archive, and attaches it directly to the Job.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id, operation="labels.zip_batch") as ctx:
        log_extra = {**ctx.log_context, "job_id": job_id}
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error("Label ZIP job not found", extra=log_extra)
                return TaskResult(TaskStatus.TERMINAL, "labels.job_not_found")

            if not job.mark_running():
                logger.info("Job %s is no longer pending (cancelled?); skipping label generation.", job_id)
                return TaskResult(TaskStatus.SKIPPED, "labels.job_not_pending")
            job.append_log("Starting label batch generation...")
            job.append_log(f"Format: {label_format} | Total assets: {len(asset_pks)}")

            try:
                assets = Asset.objects.filter(pk__in=asset_pks)
                zip_buffer, rendered = _render_batch_zip(job, assets, label_format, log_extra)

                return _finalize_label_zip(job, ctx.user, zip_buffer, rendered, len(assets))

            # broad except: task-isolation: record a safe typed task-boundary failure
            except Exception as e:
                status = classify_task_error(e)
                logger.error("Label ZIP task failed", extra={**log_extra, "exception_type": type(e).__name__})
                job.mark_failed(f"[{status.value}] labels.zip_failed")
                _try_create_notification(
                    ctx.user,
                    subject=_("Label Generation Failed"),
                    message=_("Label generation failed. Code: labels.zip_failed"),
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk),
                    log_extra=log_extra,
                    job=job,
                )
                return TaskResult(status, "labels.zip_failed", user_visible=True)
        # broad except: task-isolation: failures before Job resolution remain observable to the queue caller
        except Exception as exc:
            logger.error("Label ZIP entry failed", extra={**log_extra, "exception_type": type(exc).__name__})
            return TaskResult(classify_task_error(exc), "labels.entry_failed")


def generate_base64_barcode(asset: object, barcode_format: str | None) -> str:
    buffer = io.BytesIO()

    fmt = barcode_format.lower() if barcode_format else "code128"
    if fmt == "qr":
        # inline import: heavy-import: load barcode/QR renderers only when generating graphics
        import segno

        # Encode the bare asset tag with the itambox: scheme so QR codes
        # scan correctly off any device / host (no localhost hardcoding).
        # resolve_scanned_code() understands this format on both the audit
        # and global scan-to-find paths.
        asset_tag = getattr(asset, "asset_tag", None) or str(getattr(asset, "pk", ""))
        qr_data = f"itambox:{asset_tag}"
        qr = segno.make_qr(qr_data)
        # border=4 is the mandatory QR "quiet zone" — without it the code won't
        # scan and its edge modules visually merge with neighbouring content.
        qr.save(buffer, kind="png", scale=6, border=4)
    else:
        # inline import: heavy-import: load barcode/QR renderers only when generating graphics
        import barcode

        # inline import: heavy-import: load barcode/QR renderers only when generating graphics
        from barcode.writer import ImageWriter

        if fmt not in barcode.PROVIDED_BARCODES:
            fmt = "code128"
        CODING = barcode.get_barcode_class(fmt)
        text = getattr(asset, "asset_tag", None) or str(getattr(asset, "pk", ""))
        code = CODING(text, writer=ImageWriter())
        code.write(buffer)

    img_bytes = buffer.getvalue()
    base64_str = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/png;base64,{base64_str}"


def _default_label_card(asset, barcode_data_uri):
    """Built-in label card layout (xhtml2pdf-friendly). Used when a template
    has no custom code, or when its custom code fails to render. Reads fields
    defensively so it also works for non-Asset objects.

    All user-controlled string values (name, asset_tag, serial_number) are
    HTML-escaped before interpolation so that the |safe template tag that
    renders label_card cannot be abused for stored-XSS. barcode_data_uri is
    a base64 data: URI produced entirely by this module and is safe as-is.
    """
    name = escape(getattr(asset, "name", None) or str(asset))
    asset_tag = escape(getattr(asset, "asset_tag", "") or "")
    serial_number = escape(getattr(asset, "serial_number", "") or "")
    serial_html = f'<div class="label-card-serial">S/N: {serial_number}</div>' if serial_number else ""
    barcode_img = format_html('<img src="{}" class="label-card-barcode" />', barcode_data_uri)
    return f"""
    <table class="label-card-table">
        <tr>
            <td class="label-card-metadata">
                    <div class="label-card-title">{name}</div>
                    <div class="label-card-tag">
                        {asset_tag}
                    </div>
                    {serial_html}
            </td>
            <td class="label-card-barcode-cell">
                {barcode_img}
            </td>
        </tr>
    </table>
    """


def render_label_html(asset, label_template, barcode_data_uri):
    """Render a single label card to HTML.

    Uses the template's custom Jinja2 code when present, otherwise the built-in
    default layout. Templates receive ``asset``/``obj``, a ready-to-use
    ``barcode_img`` tag, the raw ``barcode_data_uri``, and ``barcode_format``.
    """
    if not label_template.template_code:
        return _default_label_card(asset, barcode_data_uri)

    try:
        # inline import: heavy-import: Jinja2 is needed only when rendering a custom label.
        from jinja2 import StrictUndefined
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        from markupsafe import Markup

        if len(label_template.template_code) > 64 * 1024:
            raise ValueError("label template exceeds the maximum source size")

        env = ImmutableSandboxedEnvironment(autoescape=True, undefined=StrictUndefined)
        for unsafe_filter in ("attr", "format", "format_map", "map", "pprint", "xmlattr"):
            env.filters.pop(unsafe_filter, None)
        for unsafe_global in ("cycler", "joiner", "namespace", "lipsum"):
            env.globals.pop(unsafe_global, None)
        template = env.from_string(label_template.template_code)
        status = getattr(asset, "status", None)
        location = getattr(asset, "location", None)
        asset_context = {
            "name": str(getattr(asset, "name", None) or ""),
            "asset_tag": str(getattr(asset, "asset_tag", None) or ""),
            "serial_number": str(getattr(asset, "serial_number", None) or ""),
            "location": str(getattr(location, "name", None) or location or ""),
            "status": str(getattr(status, "name", None) or status or ""),
        }
        context = {
            "obj": asset_context,
            "asset": asset_context,
            "barcode_data_uri": barcode_data_uri,
            "barcode_img": Markup(str(format_html('<img src="{}" class="label-card-barcode" />', barcode_data_uri))),
            "barcode_format": label_template.barcode_format,
        }
        rendered = template.render(**context)
        if len(rendered) > 256 * 1024:
            raise ValueError("rendered label exceeds the maximum output size")
        return sanitize_label_html_for_pdf(rendered, allowed_data_uris=frozenset({barcode_data_uri}))
    # broad except: render-degrade: invalid optional custom markup safely degrades to the built-in card
    except Exception as e:
        logger.warning(
            "Custom label rendering degraded to the default layout",
            extra={
                "operation": "labels.custom_template",
                "template_id": getattr(label_template, "pk", None),
                "exception_type": type(e).__name__,
            },
        )
        return _default_label_card(asset, barcode_data_uri)


def chunk_list(lst: Sequence[_ChunkItem], n: int) -> Iterator[Sequence[_ChunkItem]]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _build_labels_document(rendered_cards, label_template, layout_mode):
    """Wrap rendered label cards into a complete, printable HTML document sized
    for the chosen layout. Unknown layouts fall back to continuous-roll sizing."""
    base_css = _label_print_css()
    if layout_mode in ("a4_grid", "letter_grid"):
        paper_size = "a4" if layout_mode == "a4_grid" else "letter"
        margin = "10mm" if layout_mode == "a4_grid" else "0.5in"
        cell_height = "34mm" if layout_mode == "a4_grid" else "1.22in"

        # Chunk cards into pages of 24
        pages = []
        for page_cards in chunk_list(rendered_cards, 24):
            padded_cards = list(page_cards)
            while len(padded_cards) % 3 != 0:
                padded_cards.append(None)

            rows = list(chunk_list(padded_cards, 3))
            pages.append(rows)

        pages_block = ""
        for page_idx, page in enumerate(pages):
            page_break = "page-break-always" if page_idx < len(pages) - 1 else "page-break-avoid"

            rows_block = ""
            for row in page:
                cells_block = ""
                for card in row:
                    if card:
                        cells_block += f'<td class="grid-cell">{card}</td>\n'
                    else:
                        cells_block += '<td class="grid-cell">&nbsp;</td>\n'
                rows_block += f"<tr>\n{cells_block}</tr>\n"

            pages_block += '<table class="grid-table ' + page_break + '">\n' + rows_block + "</table>\n"

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        {base_css}
        @page {{
            size: {paper_size};
            margin: {margin};
        }}
        .grid-cell {{
            height: {cell_height};
        }}
    </style>
</head>
<body>
    {pages_block}
</body>
</html>"""

    # 'roll' (and any unrecognised layout): one label per page, sized to the template
    width = _safe_label_measurement(getattr(label_template, "page_width", None), 2.25)
    height = _safe_label_measurement(getattr(label_template, "page_height", None), 1.25)

    cards_block = ""
    for idx, card in enumerate(rendered_cards):
        page_break = "page-break-always" if idx < len(rendered_cards) - 1 else "page-break-avoid"
        cards_block += '<div class="label-card ' + page_break + '">' + card + "</div>\n"

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        {base_css}
        @page {{
            size: {width}in {height}in;
            margin: 0;
        }}
        .label-card {{
            width: {width}in;
            height: {height}in;
        }}
    </style>
</head>
<body>
    {cards_block}
</body>
</html>"""


def _pdf_safe_link_callback(uri, rel):
    return pdf_safe_link_callback(uri, rel)


def _html_to_pdf_bytes(html_content):
    return html_to_pdf_bytes(html_content)


def render_labels_pdf(assets, label_template, layout_mode="roll"):
    """Synchronously render one or more asset labels into a single PDF (bytes).

    Shares the exact card engine (``render_label_html``) and document builder
    used by the bulk print job, so single-label output is identical — just
    without a background Job. Suitable for inline HTTP responses.
    """
    rendered_cards = []
    for asset in assets:
        barcode_data_uri = generate_base64_barcode(asset, label_template.barcode_format)
        rendered_cards.append(render_label_html(asset, label_template, barcode_data_uri))

    if not rendered_cards:
        raise ValueError("No labels were rendered.")

    html_content = _build_labels_document(rendered_cards, label_template, layout_mode)
    return _html_to_pdf_bytes(html_content)


def _render_label_cards(job, assets, label_template, log_extra):
    """Render one HTML card per asset; one failure never aborts the batch."""
    rendered_cards = []
    for asset in assets:
        try:
            barcode_data_uri = generate_base64_barcode(asset, label_template.barcode_format)
            card_html = render_label_html(asset, label_template, barcode_data_uri)
            rendered_cards.append(card_html)
            job.append_log(f" - Rendered label for asset PK {asset.pk}.")
        # broad except: boundary-isolation: one label failure must not abort the requested batch
        except Exception as ex:
            job.append_log(f" - Asset PK {asset.pk} failed [labels.item_failed].")
            logger.warning(
                "Label rendering failed",
                extra={**log_extra, "object_id": asset.pk, "exception_type": type(ex).__name__},
            )
    return rendered_cards


def _finalize_label_pdf(job, user, log_extra, pdf_bytes, assets, rendered_cards):
    """Persist the PDF attachment, notify, and return the terminal result."""
    ct = ContentType.objects.get_for_model(Job)
    attachment = FileAttachment(
        model=ct, object_id=job.pk, name=f"labels_batch_{job.pk}.pdf", mime_type="application/pdf"
    )
    try:
        attachment.save()
        attachment.file.save(f"labels_batch_{job.pk}.pdf", ContentFile(pdf_bytes))
        attachment.save()
    # broad except: cleanup-reraise: persistence is all-or-nothing across the database
    # row and storage object, while the outer task boundary retains error classification
    except Exception:
        _cleanup_partial_attachment(job, attachment, log_extra)
        raise

    job.append_log("PDF document generated and saved successfully.")
    job.mark_completed(result={"file_name": attachment.name, "download_url": attachment.get_download_url()})

    _try_create_notification(
        user,
        subject=_("Label Generation Complete"),
        message=_("Successfully generated label PDF for %(count)s asset(s). Click to download.")
        % {"count": len(assets)},
        level=Notification.LEVEL_SUCCESS,
        target_url=attachment.get_download_url(),
        log_extra=log_extra,
        job=job,
    )
    return TaskResult(
        TaskStatus.PARTIAL if len(rendered_cards) < len(assets) else TaskStatus.SUCCESS,
        "labels.pdf_partial" if len(rendered_cards) < len(assets) else "labels.pdf_completed",
        {"rendered": len(rendered_cards), "failed": len(assets) - len(rendered_cards)},
        user_visible=True,
    )


def _resolve_label_scope(job, tenant_id):
    """Extract the persisted authorized label scope (fail-closed when absent)."""
    scope_tenant_ids = job.data.get("scope_tenant_ids") or ([tenant_id] if tenant_id is not None else [])
    return [int(scope_id) for scope_id in scope_tenant_ids]


def _resolve_label_template(job, template_id, log_extra):
    """Load the LabelTemplate for a PDF batch; terminal result when missing."""
    try:
        return LabelTemplate.objects.get(pk=template_id)
    except LabelTemplate.DoesNotExist:
        logger.error("Label template not found", extra={**log_extra, "template_id": template_id})
        job.mark_failed("[terminal] labels.template_not_found")
        return TaskResult(TaskStatus.TERMINAL, "labels.template_not_found", user_visible=True)


def generate_label_pdf_batch_task(
    job_id: int,
    asset_pks: Sequence[int | str],
    template_id: int,
    layout_mode: str,
    user_id: int | None,
    tenant_id: int | None = None,
) -> TaskResult:
    """
    Asynchronously generates a single compiled PDF of asset labels using the selected LabelTemplate
    and layout mode, and attaches it directly to the Job.
    """

    with TaskContext(tenant_id=tenant_id, user_id=user_id, operation="labels.pdf_batch") as ctx:
        log_extra = {**ctx.log_context, "job_id": job_id}
        # Boundary state: initialized before any fallible step so both failure
        # handlers can always reference a bound phase and attachment, and no
        # transient error is ever masked by an UnboundLocalError.
        phase = "job_resolve"
        attachment = None
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error("Label PDF job not found", extra=log_extra)
                return TaskResult(TaskStatus.TERMINAL, "labels.job_not_found")

            phase = "job_start"
            if not job.mark_running():
                logger.info("Job %s is no longer pending (cancelled?); skipping PDF label generation.", job_id)
                return TaskResult(TaskStatus.SKIPPED, "labels.job_not_pending")
            job.append_log("Starting asynchronous PDF label batch generation...")

            phase = "template_resolve"
            label_template = _resolve_label_template(job, template_id, log_extra)
            if isinstance(label_template, TaskResult):
                return label_template
            job.append_log("Label template resolved.")

            job.append_log(f"Layout mode: {layout_mode} | Total assets to print: {len(asset_pks)}")

            phase = "asset_resolve"
            scope_tenant_ids = _resolve_label_scope(job, tenant_id)
            assets, resolution = _resolve_label_task_assets(Asset, asset_pks, scope_tenant_ids, ctx.user)
            if resolution is not None:
                return _finish_label_asset_resolution(job, resolution)

            # Render individual cards (with per-asset logging for the job trail)
            phase = "label_render"
            rendered_cards = _render_label_cards(job, assets, label_template, log_extra)

            if not rendered_cards:
                job.mark_failed("[terminal] labels.no_labels_rendered")
                return TaskResult(TaskStatus.TERMINAL, "labels.no_labels_rendered", user_visible=True)

            job.append_log("Compiling PDF document using xhtml2pdf...")
            phase = "document_build"
            html_content = _build_labels_document(rendered_cards, label_template, layout_mode)

            phase = "pdf_render"
            pdf_bytes = _html_to_pdf_bytes(html_content)

            phase = "attachment_persist"
            return _finalize_label_pdf(job, ctx.user, log_extra, pdf_bytes, assets, rendered_cards)

        # broad except: task-isolation: record a safe typed task-boundary failure
        except Exception as e:
            status = classify_task_error(e)
            logger.error(
                "Label PDF task failed",
                extra={**log_extra, "phase": phase, "exception_type": type(e).__name__},
            )
            if "job" in locals():
                job.append_log(f"[{status.value}] labels.pdf_failed phase={phase}")
                job.mark_failed(f"[{status.value}] labels.pdf_failed")
                _cleanup_partial_attachment(job, attachment, log_extra)
            _try_create_notification(
                ctx.user,
                subject=_("Label Generation Failed"),
                message=_("Label generation failed. Code: labels.pdf_failed"),
                level=Notification.LEVEL_DANGER,
                target_url=reverse_job_detail(job.pk) if "job" in locals() else None,
                log_extra=log_extra,
                job=job if "job" in locals() else None,
            )
            return TaskResult(status, "labels.pdf_failed", user_visible=True)
