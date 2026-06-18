from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import quote
from graphene_django.views import GraphQLView
from rest_framework import exceptions

from itambox.api.authentication import TokenAuthentication
from itambox.middleware import TenantMiddleware, CurrentUserMiddleware


def field_count_limit_validator(max_fields=500, max_aliases=50):
    """Bound total field selections and aliases in a single operation.

    Depth limiting alone does not stop *breadth*: a query can stay within the
    depth cap while aliasing the same expensive root field hundreds of times
    (`a1: assets(...) a2: assets(...) ...`) to amplify DB load. This rule rejects
    operations whose field/alias counts exceed sane limits.
    """
    from graphql.validation import ValidationRule
    from graphql.error import GraphQLError

    class FieldCountLimitRule(ValidationRule):
        def __init__(self, context):
            super().__init__(context)
            self._fields = 0
            self._aliases = 0

        def enter_field(self, node, *_args):
            self._fields += 1
            if node.alias:
                self._aliases += 1
            if self._fields > max_fields:
                self.report_error(GraphQLError(
                    f'Query exceeds the maximum of {max_fields} field selections.', node))
            elif self._aliases > max_aliases:
                self.report_error(GraphQLError(
                    f'Query exceeds the maximum of {max_aliases} aliases.', node))

    return FieldCountLimitRule


def query_complexity_validator(max_complexity=1000, fan_out=10):
    """Bound the estimated execution cost of a single operation.

    Depth and field-count limits cap the *shape* of a query but not its *cost*:
    a query can stay shallow and within the field cap while still triggering
    O(N*M) database work by nesting *list*-returning fields (e.g.
    ``assets { components { manufacturer { softwareProducts { name } } } }``).
    Every list nesting multiplies the number of rows resolved, so a handful of
    selections can fan out into a denial-of-service.

    This rule accumulates a cost score while walking the query: each field
    instance costs the product of the fan-out factors of the list fields that
    enclose it. A list-returning field multiplies the cost of everything beneath
    it by ``fan_out``; a singular object relation (e.g. ``tenant``) does not,
    since it resolves a single row. A ``GraphQLError`` is reported when the
    running total exceeds ``max_complexity``.

    Fields reached through a *named fragment* are accounted for too: graphql-core
    visits each fragment definition only once at the document top level, so the
    main walk never descends into a ``...spread``. To stop a query from hiding
    fan-out inside fragments (``a0: assets { ...F } ... fragment F on Asset {
    <expensive list path> }``), :meth:`enter_fragment_spread` runs a sub-walk of
    the referenced fragment's selection set, seeded with the spread site's
    current multiplier and a ``TypeInfo`` rooted at the fragment's type
    condition, and folds the resulting cost back in at the spread site.

    Mirrors :func:`field_count_limit_validator`: a factory returning a
    ``ValidationRule`` subclass that uses the ``enter_field`` / ``leave_field``
    visitor hooks and reports via ``self.report_error(GraphQLError(...))``. The
    base ``ValidationRule`` runs under graphql-core's ``TypeInfo`` visitor, so
    ``self.context.get_type()`` yields the live output type used to detect lists.
    """
    from graphql import visit
    from graphql.language.visitor import Visitor
    from graphql.utilities import TypeInfo, TypeInfoVisitor, type_from_ast
    from graphql.validation import ValidationRule
    from graphql.error import GraphQLError
    from graphql.type import GraphQLList, GraphQLNonNull

    def _returns_list(output_type):
        """True if the field's output type is (a non-null wrapper around) a list."""
        if isinstance(output_type, GraphQLNonNull):
            output_type = output_type.of_type
        return isinstance(output_type, GraphQLList)

    class QueryComplexityValidator(ValidationRule):
        def __init__(self, context):
            super().__init__(context)
            self._cost = 0
            self._reported = False
            # Stack of cost-multipliers for the enclosing field path. The root
            # selection set has a multiplier of 1.
            self._multipliers = [1]
            # Fragment names on the current spread path, to avoid unbounded
            # recursion on cyclic fragment references (which other validation
            # rules reject, but we must not hang before they run).
            self._fragment_path = set()

        def _report_if_over(self, node):
            if self._cost > max_complexity and not self._reported:
                self._reported = True
                self.report_error(GraphQLError(
                    f'Query exceeds the maximum complexity of {max_complexity}.',
                    node))

        def enter_field(self, node, *_args):
            multiplier = self._multipliers[-1]
            # Each selected field instance costs its enclosing multiplier.
            self._cost += multiplier
            self._report_if_over(node)
            # A list-returning field amplifies everything nested beneath it.
            output_type = self.context.get_type()
            child_multiplier = multiplier
            if _returns_list(output_type):
                child_multiplier *= fan_out
            self._multipliers.append(child_multiplier)

        def leave_field(self, node, *_args):
            self._multipliers.pop()

        def enter_fragment_spread(self, node, *_args):
            # The main document walk does not expand named fragments, so charge
            # the referenced fragment's field cost here, at the spread site, using
            # the multiplier accumulated by the enclosing field path.
            name = node.name.value
            if name in self._fragment_path:
                return  # cyclic spread — bail out (other rules report the cycle)
            fragment = self.context.get_fragment(name)
            if fragment is None:
                return  # undefined fragment — other rules report it
            root_type = type_from_ast(self.context.schema, fragment.type_condition)
            if root_type is None:
                return  # unknown type condition — other rules report it

            multiplier = self._multipliers[-1]
            self._fragment_path.add(name)
            self._account_for_selection_set(fragment.selection_set, root_type, multiplier)
            self._fragment_path.discard(name)

        def _account_for_selection_set(self, selection_set, root_type, base_multiplier):
            """Walk ``selection_set`` (a fragment body) and fold its field cost
            into the running total, treating ``base_multiplier`` as the cost of a
            field selected directly on ``root_type``.

            A fresh ``TypeInfo`` seeded at ``root_type`` resolves list-vs-singular
            output types exactly as the main walk does, so nested list fan-out
            inside the fragment is multiplied identically. Nested fragment spreads
            recurse through the outer validator (shared cost/multiplier state),
            so multi-hop fragment chains are charged too.
            """
            outer = self

            class _FragmentCostVisitor(Visitor):
                def __init__(self):
                    super().__init__()
                    # Mirror the outer multiplier stack, rooted at the spread site.
                    self._stack = [base_multiplier]

                def enter_field(self, node, *_a):
                    multiplier = self._stack[-1]
                    outer._cost += multiplier
                    outer._report_if_over(node)
                    output_type = type_info.get_type()
                    child_multiplier = multiplier
                    if _returns_list(output_type):
                        child_multiplier *= fan_out
                    self._stack.append(child_multiplier)

                def leave_field(self, node, *_a):
                    self._stack.pop()

                def enter_fragment_spread(self, node, *_a):
                    # Delegate to the outer rule so the cycle guard and the
                    # spread-site multiplier (top of this visitor's stack) apply.
                    outer._multipliers.append(self._stack[-1])
                    try:
                        outer.enter_fragment_spread(node)
                    finally:
                        outer._multipliers.pop()

            type_info = TypeInfo(self.context.schema, initial_type=root_type)
            visit(selection_set, TypeInfoVisitor(type_info, _FragmentCostVisitor()))

    return QueryComplexityValidator


@method_decorator(csrf_exempt, name='dispatch')
class PrivateGraphQLView(GraphQLView):
    def __init__(self, *args, **kwargs):
        from graphql.validation import specified_rules
        from graphql.validation import NoSchemaIntrospectionCustomRule
        from graphene.validation import depth_limit_validator

        rules = list(specified_rules)
        rules.append(depth_limit_validator(max_depth=10))
        rules.append(field_count_limit_validator(max_fields=500, max_aliases=50))
        # Budget sized to admit legitimate two-list-deep reads (e.g. the
        # adversarial-suite query assets->...->softwareProducts->...->
        # softwareProducts->name, cost ~1231) while still rejecting egregious
        # fan-out such as many aliased copies of an expensive list-over-list path.
        rules.append(query_complexity_validator(max_complexity=2000, fan_out=10))

        if not settings.DEBUG:
            rules.append(NoSchemaIntrospectionCustomRule)

        kwargs['validation_rules'] = rules
        super().__init__(*args, **kwargs)

    @property
    def graphiql(self):
        return settings.DEBUG

    @graphiql.setter
    def graphiql(self, value):
        pass

    def dispatch(self, request, *args, **kwargs):
        if request.method == 'GET':
            if not request.user.is_authenticated:
                from django.shortcuts import resolve_url
                return redirect(f"{resolve_url(settings.LOGIN_URL)}?next={quote(request.get_full_path())}")
        
        elif request.method == 'POST':
            if request.user.is_authenticated:
                from django.middleware.csrf import CsrfViewMiddleware
                csrf_reject = CsrfViewMiddleware(lambda r: None).process_view(request, None, (), {})
                if csrf_reject:
                    return csrf_reject

            if not request.user.is_authenticated:
                try:
                    auth_result = TokenAuthentication().authenticate(request)
                    if auth_result is not None:
                        user, token = auth_result
                        request.user = user
                        request.auth = token
                        # Re-run current user middleware to bind context user
                        CurrentUserMiddleware().process_request(request)
                        # Re-run tenant middleware to set tenant context
                        TenantMiddleware().process_request(request)
                    else:
                        return HttpResponse(_('Unauthorized'), status=401)
                except exceptions.AuthenticationFailed as e:
                    return JsonResponse({'errors': [{'message': str(e)}]}, status=401)
                except Exception as e:
                    return JsonResponse({'errors': [{'message': str(_('Authentication failed'))}]}, status=401)
            
            # Perform rate limiting / throttling checks
            from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
            throttles = [AnonRateThrottle(), UserRateThrottle()]
            for throttle in throttles:
                if not throttle.allow_request(request, self):
                    wait = throttle.wait()
                    return JsonResponse(
                        {'errors': [{'message': str(_('Request was throttled. Expected available in %(wait)s seconds.') % {'wait': wait})}]},
                        status=429
                    )
                    
        return super().dispatch(request, *args, **kwargs)
