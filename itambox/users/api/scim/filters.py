import re
from django.db.models import Q


class SCIMFilterError(ValueError):
    """Raised when a SCIM filter expression cannot be parsed."""
    pass


# Upper bound on a SCIM filter expression before it reaches the parser regex.
# The grammar has adjacent whitespace groups (\s+ ... \s*) that backtrack
# polynomially on long crafted inputs (ReDoS); real filters are short.
MAX_SCIM_FILTER_LENGTH = 512


def _reject_oversized_filter(filter_str):
    """Raise SCIMFilterError if the expression exceeds the ReDoS length bound."""
    if len(filter_str) > MAX_SCIM_FILTER_LENGTH:
        raise SCIMFilterError("SCIM filter expression is too long.")


def parse_scim_filter(filter_str, model_type='user'):
    if not filter_str:
        return Q()

    # Normalize whitespace
    filter_str = filter_str.strip()
    if not filter_str:
        return Q()

    # Bound the input before any regex work (see MAX_SCIM_FILTER_LENGTH).
    _reject_oversized_filter(filter_str)

    # Normalize common bracketed filter paths (e.g. emails[type eq "work"].value -> email)
    filter_str = re.sub(r'emails\[type\s+eq\s+["\']?[a-zA-Z0-9_-]+["\']?\]\.value', 'email', filter_str, flags=re.IGNORECASE)

    # Parse simple expressions like: attribute operator "value" or attribute operator value
    # E.g. userName eq "test@example.com"
    # E.g. active eq true
    # E.g. displayName eq "Admins"
    # E.g. userName sw "test"
    # E.g. id eq 123

    # We match: (attribute) (operator) (value)
    # The value can be double-quoted, single-quoted, or unquoted (like true/false/numbers)
    pattern = re.compile(
        r'^\s*([a-zA-Z0-9_\.]+)\s+(eq|co|sw|ew|gt|ge|lt|le|pr)\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'\)]+))?\s*$',
        re.IGNORECASE
    )

    match = pattern.match(filter_str)
    if not match:
        raise SCIMFilterError(f"Invalid SCIM filter expression: {filter_str}")

    attr, op, val_double, val_single, val_unquoted = match.groups()
    val = val_double or val_single or val_unquoted
    op = op.lower()

    if op != 'pr' and val is None:
        raise SCIMFilterError(f"Operator '{op}' requires a value.")

    # Normalize attribute name
    attr_lower = attr.lower()

    # Map SCIM attributes to Django model fields
    field_name = None
    if model_type == 'user':
        if attr_lower == 'username':
            field_name = 'username'
        elif attr_lower in ('email', 'emails', 'emails.value'):
            field_name = 'email'
        elif attr_lower == 'active':
            field_name = 'is_active'
        elif attr_lower in ('id', 'externalid'):
            field_name = 'id'
        elif attr_lower == 'displayname':
            field_name = 'username'
    elif model_type == 'group':
        if attr_lower in ('displayname', 'name'):
            field_name = 'name'
        elif attr_lower in ('id', 'externalid'):
            field_name = 'id'

    if not field_name:
        field_name = attr

    # Convert value to correct type
    if val is not None:
        val_lower = val.lower()
        if val_lower == 'true':
            val = True
        elif val_lower == 'false':
            val = False
        elif val_lower == 'null':
            val = None
        else:
            try:
                val = int(val)
            except ValueError:
                pass

    # Build Q object based on operator
    if op == 'eq':
        if val is True or val is False or val is None:
            return Q(**{field_name: val})
        else:
            if isinstance(val, str):
                return Q(**{f"{field_name}__iexact": val})
            return Q(**{field_name: val})
    elif op == 'co':
        return Q(**{f"{field_name}__icontains": val})
    elif op == 'sw':
        return Q(**{f"{field_name}__istartswith": val})
    elif op == 'ew':
        return Q(**{f"{field_name}__iendswith": val})
    elif op == 'pr':
        return Q(**{f"{field_name}__isnull": False}) & ~Q(**{field_name: ''})
    elif op == 'gt':
        return Q(**{f"{field_name}__gt": val})
    elif op == 'ge':
        return Q(**{f"{field_name}__gte": val})
    elif op == 'lt':
        return Q(**{f"{field_name}__lt": val})
    elif op == 'le':
        return Q(**{f"{field_name}__lte": val})

    return Q()
