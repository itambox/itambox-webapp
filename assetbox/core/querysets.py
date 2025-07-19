from django.db import models
from django.db.models import Count, Subquery, OuterRef

class CustomQuerySet(models.QuerySet):
    """
    Base QuerySet providing common annotation methods like add_related_count.
    Inspired by NetBox patterns.
    """

    def add_related_count(self, related_model, related_field, count_attr):
        """
        Annotate the count of related objects onto the queryset.

        Args:
            related_model: The related model class.
            related_field: The name of the ForeignKey field on the related model
                           that points back to this queryset's model.
            count_attr: The name of the attribute to store the count annotation.
        """
        subquery = Subquery(
            related_model.objects.filter(
                **{f'{related_field}_id': OuterRef('pk')}
            ).values(
                related_field
            ).annotate(
                _count=Count('pk')
            ).values('_count')[:1]
        )

        return self.annotate(
            **{count_attr: subquery}
        ).annotate(
            **{count_attr: models.functions.Coalesce(count_attr, 0)}
        )


def count_related(model, field_name):
    return models.Count(field_name)