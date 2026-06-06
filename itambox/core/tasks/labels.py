import logging
import io
import zipfile
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile

from core.models import Job, Notification, FileAttachment
from .context import TaskContext
from .utils import reverse_job_detail

logger = logging.getLogger(__name__)

def generate_single_label_graphic(asset, label_format):
    """
    Renders QR code or Barcode PNG bytes for the given asset.
    """
    import io
    buffer = io.BytesIO()
    
    if label_format == 'qr':
        import segno
        # Generate clean QR code
        qr = segno.make_qr(f"itambox://asset/{asset.pk}")
        qr.save(buffer, kind='png', scale=10)
    else:
        import barcode
        from barcode.writer import ImageWriter
        # Generate barcode
        CODING = barcode.get_barcode_class('code128')
        # Clean text
        code = CODING(asset.asset_tag or str(asset.pk), writer=ImageWriter())
        code.write(buffer)

    return buffer.getvalue()


def generate_label_batch_task(job_id, asset_pks, label_format, user_id, tenant_id=None):
    """
    Asynchronously generates QR-codes/barcodes for selected assets,
    packages them into a ZIP archive, and attaches it directly to the Job.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error(f"Job {job_id} not found during async label printing.")
                return

            job.mark_running()
            job.append_log("Starting label batch generation...")
            job.append_log(f"Format: {label_format} | Total assets: {len(asset_pks)}")

            try:
                from assets.models import Asset
                assets = Asset.objects.filter(pk__in=asset_pks)
                
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for asset in assets:
                        try:
                            img_data = generate_single_label_graphic(asset, label_format)
                            filename = f"label_{asset.asset_tag}_{label_format}.png"
                            zip_file.writestr(filename, img_data)
                            job.append_log(f" - Rendered label for {asset.asset_tag}")
                        except Exception as ex:
                            job.append_log(f" - Error rendering label for PK {asset.pk}: {str(ex)}")

                zip_buffer.seek(0)
                
                ct = ContentType.objects.get_for_model(Job)
                attachment = FileAttachment.objects.create(
                    model=ct,
                    object_id=job.pk,
                    name=f"labels_batch_{job.pk}.zip",
                    mime_type="application/zip"
                )
                attachment.file.save(f"labels_batch_{job.pk}.zip", ContentFile(zip_buffer.getvalue()))
                attachment.save()

                job.append_log(f"ZIP package generated and saved successfully: {attachment.file.name}")
                job.mark_completed(result={
                    'file_name': attachment.name,
                    'download_url': attachment.file.url
                })

                Notification.objects.create(
                    user=ctx.user,
                    subject="Label Generation Complete",
                    message=f"Successfully generated label batch zip for {assets.count()} asset(s). Click to download.",
                    level=Notification.LEVEL_SUCCESS,
                    target_url=attachment.file.url
                )

            except Exception as e:
                logger.exception("Exception during label batch generation task")
                job.mark_failed(str(e))
                Notification.objects.create(
                    user=ctx.user,
                    subject="Label Generation Failed",
                    message=f"An error occurred during barcode rendering: {str(e)}",
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk)
                )
        except Exception as e:
            logger.exception("Outer exception during label batch generation task")


def generate_base64_barcode(asset, barcode_format):
    import io
    import base64
    buffer = io.BytesIO()

    fmt = barcode_format.lower() if barcode_format else 'code128'
    if fmt == 'qr':
        import segno
        # Generate QR code pointing to the asset URL
        qr_data = f"http://localhost:8000{asset.get_absolute_url()}"
        qr = segno.make_qr(qr_data)
        qr.save(buffer, kind='png', scale=4, border=0)
    else:
        import barcode
        from barcode.writer import ImageWriter
        if fmt not in barcode.PROVIDED_BARCODES:
            fmt = 'code128'
        CODING = barcode.get_barcode_class(fmt)
        text = asset.asset_tag or str(asset.pk)
        code = CODING(text, writer=ImageWriter())
        code.write(buffer)

    img_bytes = buffer.getvalue()
    base64_str = base64.b64encode(img_bytes).decode('utf-8')
    return f"data:image/png;base64,{base64_str}"


def render_label_html(asset, label_template, barcode_data_uri):
    # Detect if the template code is ZPL or invalid
    is_zpl = False
    if label_template.template_code:
        cleaned_tpl = label_template.template_code.strip()
        if cleaned_tpl.startswith('^XA') or '^XA' in cleaned_tpl:
            is_zpl = True

    if is_zpl or not label_template.template_code:
        # Render clean default HTML table layout compatible with xhtml2pdf
        serial_html = f'<div style="font-size: 7pt; font-weight: normal; color: #555;">S/N: {asset.serial_number}</div>' if asset.serial_number else ''
        return f"""
        <table style="width: 100%; border-collapse: collapse; margin: 0; padding: 0;">
            <tr>
                <td style="width: 55%; vertical-align: middle; text-align: left; padding: 2px;">
                    <div style="font-family: Helvetica, Arial, sans-serif; font-size: 8pt; font-weight: bold; color: #000; line-height: 1.1;">
                        <div style="font-size: 9.5pt; margin-bottom: 3px; max-height: 24pt; overflow: hidden;">{asset.name}</div>
                        <div style="font-size: 8pt; font-family: monospace; background-color: #000; color: #fff; padding: 1px 3px; display: inline-block; border-radius: 2px; margin-bottom: 4px;">
                            {asset.asset_tag or ''}
                        </div>
                        {serial_html}
                    </div>
                </td>
                <td style="width: 45%; vertical-align: middle; text-align: right; padding: 2px;">
                    <img src="{barcode_data_uri}" style="width: 0.95in; height: 0.95in; display: block; margin-left: auto;" />
                </td>
            </tr>
        </table>
        """
    else:
        # Render using the custom template code in the database
        try:
            from jinja2.sandbox import SandboxedEnvironment
            env = SandboxedEnvironment()
            template = env.from_string(label_template.template_code)
            context = {
                'obj': asset,
                'asset': asset,
                'barcode_data_uri': barcode_data_uri,
                'barcode_img': f'<img src="{barcode_data_uri}" style="width: 0.9in; height: 0.9in;" />',
                'barcode_format': label_template.barcode_format,
            }
            return template.render(**context)
        except Exception as e:
            logger.warning(f"Error rendering custom template {label_template.name}: {e}. Falling back to default layout.")
            serial_html = f'<div style="font-size: 7pt; font-weight: normal; color: #555;">S/N: {asset.serial_number}</div>' if asset.serial_number else ''
            return f"""
            <table style="width: 100%; border-collapse: collapse; margin: 0; padding: 0;">
                <tr>
                    <td style="width: 55%; vertical-align: middle; text-align: left; padding: 2px;">
                        <div style="font-family: Helvetica, Arial, sans-serif; font-size: 8pt; font-weight: bold; color: #000; line-height: 1.1;">
                            <div style="font-size: 9.5pt; margin-bottom: 3px; max-height: 24pt; overflow: hidden;">{asset.name}</div>
                            <div style="font-size: 8pt; font-family: monospace; background-color: #000; color: #fff; padding: 1px 3px; display: inline-block; border-radius: 2px; margin-bottom: 4px;">
                                {asset.asset_tag or ''}
                            </div>
                            {serial_html}
                        </div>
                    </td>
                    <td style="width: 45%; vertical-align: middle; text-align: right; padding: 2px;">
                        <img src="{barcode_data_uri}" style="width: 0.95in; height: 0.95in; display: block; margin-left: auto;" />
                    </td>
                </tr>
            </table>
            """


def chunk_list(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def generate_label_pdf_batch_task(job_id, asset_pks, template_id, layout_mode, user_id, tenant_id=None):
    """
    Asynchronously generates a single compiled PDF of asset labels using the selected LabelTemplate
    and layout mode, and attaches it directly to the Job.
    """
    from xhtml2pdf import pisa
    from assets.models import Asset
    from core.models import LabelTemplate

    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error(f"Job {job_id} not found during async PDF label printing.")
                return

            job.mark_running()
            job.append_log("Starting asynchronous PDF label batch generation...")

            try:
                label_template = LabelTemplate.objects.get(pk=template_id)
                job.append_log(f"Selected template: {label_template.name} ({label_template.page_width}x{label_template.page_height} in)")
            except LabelTemplate.DoesNotExist:
                error_msg = f"LabelTemplate {template_id} not found."
                logger.error(error_msg)
                job.mark_failed(error_msg)
                return

            job.append_log(f"Layout mode: {layout_mode} | Total assets to print: {len(asset_pks)}")

            assets = list(Asset.objects.filter(pk__in=asset_pks))
            if not assets:
                job.append_log("No matching assets found to print.")
                job.mark_completed(result={'status': 'no_assets'})
                return

            # Render individual cards
            rendered_cards = []
            for asset in assets:
                try:
                    # Generate base64 barcode image
                    barcode_data_uri = generate_base64_barcode(asset, label_template.barcode_format)
                    # Render label HTML (custom or default fallback)
                    card_html = render_label_html(asset, label_template, barcode_data_uri)
                    rendered_cards.append(card_html)
                    job.append_log(f" - Rendered label for {asset.asset_tag or asset.name}")
                except Exception as ex:
                    job.append_log(f" - Error rendering label for asset {asset.pk}: {str(ex)}")

            if not rendered_cards:
                raise Exception("No labels were successfully rendered.")

            # Compile into full HTML document based on layout mode
            html_content = ""
            if layout_mode == 'roll':
                width = label_template.page_width or 2.25
                height = label_template.page_height or 1.25
                
                cards_block = ""
                for idx, card in enumerate(rendered_cards):
                    page_break = "page-break-after: always;" if idx < len(rendered_cards) - 1 else "page-break-after: avoid;"
                    cards_block += f'<div class="label-card" style="{page_break}">{card}</div>\n'

                html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        @page {{
            size: {width}in {height}in;
            margin: 0;
        }}
        body {{
            margin: 0;
            padding: 0;
            background-color: #ffffff;
            font-family: Helvetica, Arial, sans-serif;
        }}
        .label-card {{
            width: {width}in;
            height: {height}in;
            box-sizing: border-box;
            padding: 0.1in;
            overflow: hidden;
        }}
    </style>
</head>
<body>
    {cards_block}
</body>
</html>"""

            elif layout_mode in ('a4_grid', 'letter_grid'):
                paper_size = "a4" if layout_mode == 'a4_grid' else "letter"
                margin = "10mm" if layout_mode == 'a4_grid' else "0.5in"
                cell_height = "34mm" if layout_mode == 'a4_grid' else "1.22in"

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
                    page_break = "page-break-after: always;" if page_idx < len(pages) - 1 else "page-break-after: avoid;"
                    
                    rows_block = ""
                    for row in page:
                        cells_block = ""
                        for card in row:
                            if card:
                                cells_block += f'<td class="grid-cell">{card}</td>\n'
                            else:
                                cells_block += '<td class="grid-cell">&nbsp;</td>\n'
                        rows_block += f'<tr>\n{cells_block}</tr>\n'
                    
                    pages_block += f'<table class="grid-table" style="{page_break}">\n{rows_block}</table>\n'

                html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        @page {{
            size: {paper_size};
            margin: {margin};
        }}
        body {{
            margin: 0;
            padding: 0;
            background-color: #ffffff;
            font-family: Helvetica, Arial, sans-serif;
        }}
        .grid-table {{
            width: 100%;
            height: 100%;
            border-collapse: collapse;
        }}
        .grid-cell {{
            width: 33.33%;
            height: {cell_height};
            border: 1px dashed #cccccc;
            padding: 2mm;
            vertical-align: middle;
            box-sizing: border-box;
            overflow: hidden;
        }}
    </style>
</head>
<body>
    {pages_block}
</body>
</html>"""

            # Render PDF bytes using xhtml2pdf
            pdf_buffer = io.BytesIO()
            job.append_log("Compiling PDF document using xhtml2pdf...")
            pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)
            
            if pisa_status.err:
                raise Exception(f"xhtml2pdf rendering failed with status code {pisa_status.err}")

            pdf_buffer.seek(0)
            pdf_bytes = pdf_buffer.getvalue()

            # Save FileAttachment
            ct = ContentType.objects.get_for_model(Job)
            attachment = FileAttachment.objects.create(
                model=ct,
                object_id=job.pk,
                name=f"labels_batch_{job.pk}.pdf",
                mime_type="application/pdf"
            )
            attachment.file.save(f"labels_batch_{job.pk}.pdf", ContentFile(pdf_bytes))
            attachment.save()

            job.append_log(f"PDF document generated and saved successfully: {attachment.file.name}")
            job.mark_completed(result={
                'file_name': attachment.name,
                'download_url': attachment.file.url
            })

            Notification.objects.create(
                user=ctx.user,
                subject="Label Generation Complete",
                message=f"Successfully generated label PDF for {len(assets)} asset(s). Click to download.",
                level=Notification.LEVEL_SUCCESS,
                target_url=attachment.file.url
            )

        except Exception as e:
            logger.exception("Exception during label batch generation task")
            if 'job' in locals():
                job.mark_failed(str(e))
            Notification.objects.create(
                user=ctx.user,
                subject="Label Generation Failed",
                message=f"An error occurred during PDF rendering: {str(e)}",
                level=Notification.LEVEL_DANGER,
                target_url=reverse_job_detail(job.pk) if 'job' in locals() else None
            )
