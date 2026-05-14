import graphene

class DocuSignStatusType(graphene.ObjectType):
    envelope_id = graphene.String()
    status = graphene.String()
    recipient = graphene.String()
    last_updated = graphene.String()

class Query(graphene.ObjectType):
    docusign_status = graphene.Field(DocuSignStatusType, envelope_id=graphene.String(required=True))

    def resolve_docusign_status(self, info, envelope_id):
        return DocuSignStatusType(
            envelope_id=envelope_id,
            status="Sent",
            recipient="test@example.com",
            last_updated="2026-06-04 10:15"
        )
