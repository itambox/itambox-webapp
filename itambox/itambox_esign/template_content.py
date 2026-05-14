from itambox.plugins.views import PluginTemplateContent
from .models import DocuSignEnvelope

class AssetDocuSignContent(PluginTemplateContent):
    model = 'assets.asset'

    def buttons(self):
        asset = self.context.get('object')
        if not asset:
            return ''

        # Check if asset is actively checked out to a user
        assignment = asset.assignments.filter(is_active=True).first()
        if not assignment or not assignment.assigned_user:
            return ''

        # Check if there is already a completed signature to avoid duplicate requests
        completed_envelope = DocuSignEnvelope.objects.filter(asset=asset, status='completed').exists()
        if completed_envelope:
            return ''

        csrf_token = self.context.get('csrf_token', '')
        return f"""
        <form method="post" action="/plugins/itambox_esign/send/{asset.id}/" style="display: inline-block;">
            <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
            <button type="submit" class="btn btn-outline-primary d-flex align-items-center">
                <i class="mdi mdi-file-sign me-1"></i> Request Signature
            </button>
        </form>
        """

    def left_panel(self):
        asset = self.context.get('object')
        if not asset:
            return ''

        envelopes = DocuSignEnvelope.objects.filter(asset=asset)
        if not envelopes.exists():
            return ''

        rows = []
        for env in envelopes:
            status_badge = ""
            if env.status == 'completed':
                status_badge = '<span class="badge bg-success-lt text-success">Completed</span>'
            elif env.status == 'declined':
                status_badge = '<span class="badge bg-danger-lt text-danger">Declined</span>'
            else:
                status_badge = f'<span class="badge bg-warning-lt text-warning">{env.status.title()}</span>'

            doc_link = ""
            if env.signed_document:
                doc_link = f'<a href="{env.signed_document.file.url}" class="btn btn-sm btn-outline-secondary py-0">View Signed PDF</a>'
            else:
                doc_link = '<span class="text-muted small">Pending Signature</span>'

            rows.append(f"""
            <tr>
                <td class="small text-muted">{env.envelope_id[:8]}...</td>
                <td>{env.recipient_name}</td>
                <td>{status_badge}</td>
                <td>{env.sent_at.strftime('%Y-%m-%d %H:%M')}</td>
                <td>{doc_link}</td>
            </tr>
            """)

        rows_html = "\n".join(rows)

        return f"""
        <div class="card mb-3 shadow-sm">
            <div class="card-header border-0 bg-transparent pb-0">
                <h3 class="card-title text-secondary">DocuSign Handover History</h3>
            </div>
            <div class="table-responsive">
                <table class="table table-vcenter card-table table-sm">
                    <thead>
                        <tr>
                            <th>Envelope ID</th>
                            <th>Recipient</th>
                            <th>Status</th>
                            <th>Sent At</th>
                            <th>Signed Document</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>
        """
