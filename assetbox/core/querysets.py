from django.db import models
from django.db.models import Count, Subquery, OuterRef

class CustomQuerySet(models.QuerySet):
    """
    Base QuerySet providing common annotation methods like add_related_count.
    Inspired by NetBox patterns.
    """

    def add_related_count(self, related_model, related_field, count_attr, cumulative=False):
        """
        Annotate the count of related objects onto the queryset.

        Args:
            related_model: The related model class.
            related_field: The name of the ForeignKey field on the related model
                           that points back to this queryset's model.
            count_attr: The name of the attribute to store the count annotation.
            cumulative: If True, include counts from descendant objects (requires
                        the model to have MPTT fields like 'lft', 'rght').
                        *Note: Cumulative functionality is NOT implemented yet.*
        """
        if cumulative:
            # TODO: Implement cumulative counting logic if MPTT is used.
            # This typically involves filtering related_model where its 'lft'/'rght'
            # falls within the parent's 'lft'/'rght'.
            # For now, raise an error or default to non-cumulative.
            # raise NotImplementedError("Cumulative related count not yet implemented.")
            # Defaulting to non-cumulative for now:
            pass # Fall through to standard annotation

        # Standard non-cumulative annotation using Subquery
        subquery = Subquery(
            related_model.objects.filter(
                **{f'{related_field}_id': OuterRef('pk')}
            ).values(
                related_field # Group by the foreign key
            ).annotate(
                _count=Count('pk') # Count related objects for this FK
            ).values('_count')[:1] # Select only the count
        )

        return self.annotate(
            **{count_attr: subquery}
        ).annotate(
            **{count_attr: models.functions.Coalesce(count_attr, 0)} # Replace None with 0
        ) 