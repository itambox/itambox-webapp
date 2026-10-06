# REST parity decisions for GraphQL-only writes (#612)

Status: decision record. No behaviour change. Baseline: `origin/main` at `9184b369`.
Part of #612 (and #602). Authorization follows #611.

## 1. Headline findings

1. The issue table is partly stale. Of the 12 operations listed as "GraphQL-only", 6 already have a REST endpoint on `main` (composition set and preview, `setCategoryDefaults`, `applyCategoryDefaults`, history cleanup and its preview). 6 have none: `previewApplyCategoryDefaults` and the five choice-set operations. `previewAssetTypeCreate` (also on the issue) has none either.
2. The definition resources `CustomField` and `CustomFieldset` are exposed by plain `ITAMBoxModelViewSet`s whose serializers do not call the definition commands (`extras/api/serializers.py` has no `create_custom_field*` call; `extras/services/definition_commands.py` is reached only from `assets/graphql_specifications/mutations.py` and `extras/definition_views.py`). "Verify parity" therefore resolves to: parity does not hold, and a port is required before the mutations can go.
3. No frontend code calls a mutation. `static/src/graphql-ui.js` is only the GraphiQL shell; `static/src/boost-guard.ts` only lists `/graphql` as a non-boosted path. There is no management command or non-test Python caller of any mutation. The UI forms call the service commands directly (for example `assets/forms/assettype_form.py:900` calls `preview_apply_category_defaults`), so they are unaffected by removing the mutations.
4. XM-06 disappears with the mutations (see section 6). It needs a REST regression test, not a typing fix.

## 2. Inventory (54 mutations)

| App | File | Lines | Mutations (count) | Input types |
|---|---|---|---|---|
| assets | `assets/schema.py` | 639 (`CreateAsset` 485, `UpdateAsset` 541, `DeleteAsset` 598, `Mutation` root 613 to 639) | 3 CRUD + 23 re-exported specification mutations = 26 | inline `Arguments` |
| assets specifications | `assets/graphql_specifications/mutations.py` | 2159 | 23 | `inputs.py` (341 lines): 27 `InputObjectType`s |
| inventory | `inventory/schema.py` | 687 | 12: accessory 249/298/349, consumable 366/412/456, kit 473/509/541, component 558/607/655 | inline `Arguments` |
| licenses | `licenses/schema.py` | 214 | 3: 85/142/196 | inline |
| software | `software/schema.py` | 180 | 3: 73/114/160 | inline |
| subscriptions | `subscriptions/schema.py` | 588 | 10: create 223, update 305, suspend 402, resume 412, renew 422, cancel 436, delete 456, assignment create 476 / update 516 / delete 552 | inline |
| wiring | `core/schema.py` | 56 (`mutation_bases` 25 to 54) | root `Mutation` composition; plugin schema modules may add a `Mutation` base | n/a |

Total 26 + 12 + 3 + 3 + 10 = 54. Specification mutations by class and start line in `mutations.py`:

`PreviewAssetTypeCreate` 960, `CreateAssetType` 980, `UpdateAssetTypeSpecifications` 1055, `PreviewApplyCategoryDefaults` 1094, `ApplyCategoryDefaults` 1124, `SetAssetTypeComposition` 1173, `UpdateAssetSpecifications` 1214, `SetCategoryDefaults` 1270, `PreviewSpecificationHistoryCleanup` 1304, `CreateSpecificationField` 1422, `UpdateSpecificationFieldPolicy` 1447, `CreateSpecificationFieldset` 1500, `UpdateSpecificationFieldset` 1518, `CreateChoiceSet` 1589, `UpdateChoiceSet` 1631, `AddChoice` 1682, `UpdateChoice` 1722, `ReorderChoices` 1837, `PreviewAssetTypeComposition` 1860, `CleanupSpecificationHistory` 1910, `PreviewLibrary` 2018, `ApplyLibrary` 2043, `ExportLibrary` 2100.

Whole-file deletions once the mutations are gone: `assets/graphql_specifications/mutations.py` (2159) and the input types in `inputs.py` (341, minus any input still referenced by queries; `scalars.py` 220, `types.py` 496, `readers.py` 154, `loaders.py` 373, `integration.py` 472 need a per-file reachability check by the removal card because query types may share them).

## 3. Per-operation decisions (GraphQL-only or contested writes)

| GraphQL operation | REST today | Decision | Endpoint |
|---|---|---|---|
| `setAssetTypeComposition` | exists | Already covered | `PUT /api/assets/asset-types/{id}/composition/` |
| `previewAssetTypeComposition` | exists | Already covered | `POST /api/assets/asset-types/{id}/composition-preview/` |
| `setCategoryDefaults` | exists | Already covered | `PUT /api/assets/categories/{id}/default-fieldsets/` |
| `applyCategoryDefaults` | exists | Already covered | `POST /api/assets/asset-types/{id}/apply-category-defaults/` |
| `previewApplyCategoryDefaults` | missing (`apply_category_defaults_preview` is already reserved in `SpecificationActionPermissions.CHANGE_ACTIONS`, but no action is registered) | REST endpoint | `POST /api/assets/asset-types/{id}/apply-category-defaults-preview/` |
| `previewAssetTypeCreate` | missing (REST create requires `preview_token` for default-consuming creates but offers no way to obtain it) | REST endpoint | `POST /api/assets/asset-types/create-preview/` |
| `cleanupSpecificationHistory` | exists for Asset and AssetType | Already covered | `POST /api/assets/assets/{id}/specification-history/cleanup/` and `POST /api/assets/asset-types/{id}/specification-history/cleanup/` |
| `previewSpecificationHistoryCleanup` | exists for Asset and AssetType | Already covered | `.../specification-history/cleanup-preview/` on both |
| `createChoiceSet` | missing | REST endpoint | `POST /api/extras/custom-field-choice-sets/` |
| `updateChoiceSet` (label, lifecycle) | missing | REST endpoint | `PATCH /api/extras/custom-field-choice-sets/{id}/` and `POST .../{id}/deprecate/` |
| `addChoice` | missing | REST endpoint | `POST /api/extras/custom-field-choice-sets/{id}/choices/` |
| `updateChoice` (label, lifecycle) | missing | REST endpoint | `PATCH /api/extras/custom-field-choices/{id}/` and `POST .../{id}/deprecate/` |
| `reorderChoices` | missing | REST endpoint | `POST /api/extras/custom-field-choice-sets/{id}/reorder/` |
| `createSpecificationField`, `updateSpecificationFieldPolicy` | `CustomFieldViewSet` exists but does not route through definition commands | Port: route serializer create/update through `create_custom_field` / `update_custom_field` (`definition_commands.py` lines 110, 174, 213) | `POST/PATCH /api/extras/custom-fields/` (existing) |
| `createSpecificationFieldset`, `updateSpecificationFieldset` | same situation | Port through `create_custom_fieldset` / `update_custom_fieldset` (lines 294, 339, 384, 425) | `POST/PATCH /api/extras/custom-fieldsets/` (existing) |
| `createAssetType`, `updateAssetTypeSpecifications` | `AssetTypeSerializer.create/update` call `create_asset_type` / `update_asset_type_specifications` | Already covered | `POST/PATCH /api/assets/asset-types/` |
| `updateAssetSpecifications` | `AssetSerializer.update` calls `update_asset_specifications` | Already covered | `PATCH /api/assets/assets/{id}/` |
| `previewLibrary`, `applyLibrary`, `exportLibrary` | `assets/api/type_library.py` | Already covered | `POST /api/assets/type-libraries/{preview,apply,export}/` |
| asset, inventory, license, software, subscription CRUD and lifecycle | `ITAMBoxModelViewSet`s; subscription `suspend`/`resume`/`renew`/`cancel` are `@action`s | Already covered | existing routers |

UI-only is rejected for every missing operation: the UI already uses the same command layer, an API consumer has no other way to manage choice lists after mutations are removed, and the issue's acceptance criteria require parity.

## 4. New REST endpoints: specification

Common rules: command layer is the single implementation (same functions the UI and the removed mutations call); errors use `error_response` / `command_result_response` from `assets/api/specification_api.py` (existing issue payload and status mapping); `StrictInputSerializer` rejects unknown keys; optimistic concurrency uses `If-Match` for the resource revision and a body field for the definition revision, exactly as the sibling endpoints do; successful writes return the new revision in `ETag`.

### 4.1 `POST /api/assets/asset-types/{id}/apply-category-defaults-preview/`

- Request: `{"specification_patch": {...}?}` (`SpecificationPatchInputSerializer`); header `If-Match: <resource revision>` required (`missing_precondition_response` otherwise).
- Response 200: `preview_payload(result)` shape, same as `composition-preview/`: token, `expected_definition_revision`, `expected_category_default_snapshot_revision`, issues, impact.
- Command: `preview_apply_category_defaults(actor, asset_type_id, expected_resource_revision, patch)`.
- Auth: `SpecificationActionPermissions`; action name already in `CHANGE_ACTIONS`, so it needs `assets.change_assettype`. Matches the mutation (command-level authorization with the actor).

### 4.2 `POST /api/assets/asset-types/create-preview/` (`detail=False`)

- Request: the native create fields of `AssetTypeSerializer` (write-only subset), `fieldsets?`, `specification_patch?`. No image upload (the mutation also forces `staged_image_id=None`).
- Response 200: preview payload (token, definition revision, category-default snapshot revision, issues).
- Command: `preview_asset_type_create(actor, native, fieldsets, patch)`; reuse `AssetTypeSerializer._native_input`.
- Auth: needs `assets.add_assettype` (create semantics). `SpecificationActionPermissions.CHANGE_ACTIONS` must NOT include this action, so it falls to the POST to `add_` default. The mutation enforced the same through the command.
- Router naming: collection action under the existing `asset-types` viewset, so `url_name` is `assettype-create-preview`.

## 5. New REST endpoints: choice sets and choices

Authorization (the important rule): the removed mutations and the UI do not use tenant-object permissions. `definition_commands.py` calls `authorize_locked(actor, <Model>, "<add|change>_<model>", ...)`, which accepts a superuser or a user/group that holds the model permission globally (`has_global_model_permission`). The UI additionally requires an all-accessible scope for global definitions (`extras/definition_views.py`, `_DefinitionPermissionMixin`). The REST endpoints must keep that exact rule. `TokenPermissions` alone is tenant-aware and would admit a tenant-scoped grant, so a new permission class `DefinitionActionPermissions(TokenPermissions)` is specified: `GET` needs `view_*`, create actions need `add_*`, every other write needs `change_*`, and write actions additionally require the global-scope check used by the UI mixin. Tests must assert denial for a tenant-scoped role holding the permission (security-test-expectations: RBAC).

Resources and serializers (new, in `extras/api/serializers.py` and `extras/api/views.py`, registered in `extras/api/urls.py` on `ITAMBoxRouter`):

| Resource | Router prefix | Model | Notes |
|---|---|---|---|
| choice set | `custom-field-choice-sets` | `CustomFieldChoiceSet` | identity `namespace/slug`; read serializer exposes `id, namespace, slug, label, lifecycle, replaced_by, choices[], resource_revision` and `ETag` |
| choice | `custom-field-choices` | `CustomFieldChoice` | read-only list/detail plus the `PATCH`/`deprecate` actions below |

| Operation | Method and path | Request body | Response | Validation / command |
|---|---|---|---|---|
| Create choice set | `POST /api/extras/custom-field-choice-sets/` | `{"namespace": str, "slug": str, "label": str, "choices": [{"key": str, "label": str}, ...]}` | 201, set with choices, `ETag` | `create_custom_field_choice_set`, then `create_custom_field_choice` per entry with `position` 1..n inside one `transaction.atomic()` (same as `CreateChoiceSet`); duplicate keys rejected; identity charset per `validate_namespace` / `validate_field_key` |
| Update choice set | `PATCH /api/extras/custom-field-choice-sets/{id}/` | `{"label": str}` | 200, set, new `ETag` | `If-Match` required; `update_custom_field_choice_set`; `lifecycle` is not accepted here |
| Deprecate choice set | `POST /api/extras/custom-field-choice-sets/{id}/deprecate/` | `{}` | 200, set | `If-Match` required; `deprecate_custom_field_choice_set`; reactivation stays rejected (`IMMUTABLE_DEFINITION`, as in `UpdateChoiceSet`); the mutation's unused `impactToken` is dropped |
| Add choice | `POST /api/extras/custom-field-choice-sets/{id}/choices/` | `{"key": str, "label": str}` | 201, choice, set `ETag` | `If-Match` of the set required (stale gives `STALE_RESOURCE`); position is last+1 (as `AddChoice`); `create_custom_field_choice` |
| Update choice | `PATCH /api/extras/custom-field-choices/{id}/` | `{"label": str}` | 200, choice, `ETag` | `If-Match` required; `update_custom_field_choice` |
| Deprecate choice | `POST /api/extras/custom-field-choices/{id}/deprecate/` | `{}` | 200, choice | `If-Match` required; `deprecate_custom_field_choice` |
| Reorder choices | `POST /api/extras/custom-field-choice-sets/{id}/reorder/` | `{"keys": [str, ...]}` | 200, set with ordered choices, `ETag` | `If-Match` of the set required; `keys` must be a duplicate-free permutation of the current keys (`REFERENCE_CONFLICT` otherwise); apply positions then touch the set inside one transaction (logic of `_prepare_choice_reorder` / `_execute_choice_reorder`, which moves to a service function shared with the UI) |

Error shape: identical to the specification endpoints (`error_response`: HTTP status derived from the first issue code, body `{"code", "issues": [{"code", "path", "message"}]}`); 412/428 for missing or stale preconditions via `missing_precondition_response`.

OpenAPI: each action gets an explicit `extend_schema` request/response serializer; `make openapi-check` baseline is regenerated in the implementing PR.

## 6. XM-06: money typed as decimals

The defect is `graphene.Float` on money arguments: the value is parsed to a binary float and then fails or loses precision against `DecimalField(max_digits=10, decimal_places=2)`. Affected arguments (9):

| Mutation | Arguments | Model field |
|---|---|---|
| `createAsset`, `updateAsset` (`assets/schema.py` 496/497, 553/554) | `purchaseCost`, `salvageValue` | `Asset.purchase_cost`, `Asset.salvage_value` |
| `createLicense`, `updateLicense` (`licenses/schema.py` 93, 151) | `purchaseCost` | `License.purchase_cost` |
| `createSubscription`, `updateSubscription`, `renewSubscription` (`subscriptions/schema.py` 232, 315, 426) | `renewalCost` | `Subscription.renewal_cost` |

Decision: removal resolves XM-06; no `graphene.Decimal` retrofit is made on code that is being deleted. The REST serializers already map these fields to `DecimalField` through `ModelSerializer` (`assets/api/serializers.py` 368/369, `licenses/api/serializers.py` 57, `subscriptions/api/serializers.py` 88). The subscription `renew` action takes `renewal_cost` through `SubscriptionRenewSerializer`. The removal card must add one REST regression test per field asserting that both the JSON string `"19.99"` and the JSON number `19.99` are stored exactly as `Decimal("19.99")` and that a three-decimal value is rejected with a field error. Query-side money fields stay as they are.

If the removal of the mutations were split across several PRs, any mutation that stays must switch these arguments to `graphene.Decimal` in the same PR that touches it.

## 7. Caller dispositions

No frontend (`static/src`), template, management command or non-test Python module invokes a mutation. Dispositions:

| Caller | Location | Disposition |
|---|---|---|
| GraphiQL shell | `static/src/graphql-ui.js`, `static/src/boost-guard.ts`, `templates/layout.html` | Keep. Queries stay; GraphiQL needs no change. |
| Type-create / category-default forms | `assets/forms/assettype_form.py` (service commands) | Unaffected, they call the command layer. |
| Choice-set UI | `extras/definition_views.py` (service commands) | Unaffected. `_prepare_choice_reorder` / `_execute_choice_reorder` (mutations.py 1782/1810) move to a shared service module before deletion. |
| Mutation error message | `assets/schema.py:564` ("Asset Type changes must use updateAssetSpecifications") | Removed with `UpdateAsset`. |
| Developer guide | `docs/integration/developer_guide.md` lines 8, 241 to 264 | Remove mutation example; state that writes are REST-only. |
| Custom fields guide | `docs/usage/custom-fields.md` lines 235 to 240 (`setAssetTypeComposition`) | Rewrite to the REST `PUT .../composition/`; lines 334 to 352 (`exportLibrary` GraphQL example) removed, the REST export section stays. |
| OpenAPI artifacts | `schema.yaml`, `tests/openapi_client/schema.d.ts` | Regenerate (`make openapi-check`). |
| flake8 baseline | `scripts/flake8_baseline.json` | Regenerate on Python 3.12 after deletions. |

Tests (path, lines): `assets/tests/test_graphql_specification_mutation_transport.py` 1583: delete; mutation cases only. `assets/tests/test_graphql_specification_http.py` 944: delete mutation cases (setCategoryDefaults 211, previewAssetTypeCreate 252/283, createAssetType 313/352, setAssetTypeComposition 393/473, updateAssetSpecifications 435/524/546/568, createChoiceSet 660, addChoice 686/713); convert to REST where `assets/tests/test_specification_rest_api.py` does not already cover it. `assets/tests/test_graphql_mutation_helpers.py` 234: delete. `assets/tests/test_graphql.py` 867 and `assets/tests/test_graphql_adversarial.py` 584: keep query cases, delete mutation cases. `inventory/tests/test_graphql_global_guard.py` 113, `software/tests/test_graphql.py` 75, `subscriptions/tests/test_graphql.py` 345, `tests/journeys/test_graphql.py` 44: keep queries, delete or convert mutation cases; add a test that `mutation` documents are rejected. `tests/e2e/spec/legacy-smoke/graphql-api.spec.ts` 339: cases 8, 9 and the mutation case near line 240 become "mutation rejected" assertions. Other files matched by the grep (compliance, core, users, procurement, organization tests, `assets/services/__init__.py`, `compliance/models.py`, `itambox/views/generic/extensions.py`) match on generic words such as "mutation" or `createAsset(` in service or test helper contexts and need no change; the removal card re-greps after deletion to prove it.

Fixtures: no fixture, seed or management command references a mutation.

## 8. Rejecting mutations (acceptance criterion 1)

Preferred: build the schema with `query` only (remove `mutation=Mutation` in `core/schema.py:56`, delete `mutation_bases`). GraphQL then answers any `mutation` document with a standard validation error ("Schema is not configured for mutations"). Plugins that contribute a `Mutation` base (core/schema.py line 44 to 45) need a compatibility note in the changelog. Fallback if a plugin API must keep the root type: a validation rule beside `specified_rules` in `core/views/graphql.py` that rejects `OperationType.MUTATION`.

## 9. Work breakdown for the removal cards

1. REST gaps: sections 4 and 5 (two endpoints plus the choice-set resource family and `DefinitionActionPermissions`); port `CustomField`/`CustomFieldset` viewsets to the definition commands; move reorder logic to a service.
2. Remove mutations per app (assets/specifications, inventory, licenses, software, subscriptions), tests, docs, changelog entry; reject mutations in the schema.
3. XM-06 REST regression tests (section 6); OpenAPI regeneration; flake8 baseline regeneration.

Items 1 must merge before item 2 deletes the choice-set and category-default preview mutations.
