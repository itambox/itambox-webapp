# Module Maturity

ITAMbox modules are graded **Stable** or **Beta**. The grade appears as a badge in the navigation menu and as a dismissible banner on list views.

## What the grades mean

| Grade | Meaning |
|-------|---------|
| **Stable** | The data model and API are more settled and receive compatibility review. Before the first tagged compatibility baseline, this grade does not guarantee a deprecation cycle across arbitrary source revisions. |
| **Beta** | The module is functional and in active use, but the data model, API shape, or feature set may change between minor releases without a separate deprecation notice. Migrations may be required. Feedback is actively sought. |

Beta is an interface-maturity label, not a data-safety or production-support guarantee. Evaluate the specific deployed revision and its documented limitations before production use.

## Current grades

### Stable

| Module | App label |
|--------|-----------|
| Assets | `assets` |
| Inventory & Stock | `inventory` |
| Organization | `organization` |
| Compliance (custody + audits) | `compliance` |
| Licenses | `licenses` |
| Software catalogue | `software` |
| Customization (tags, custom fields, dashboards) | `extras` |
| Users & Auth | `users` |

### Beta

| Module | App label / area | Reason |
|--------|-----------------|--------|
| SaaS Subscriptions | `subscriptions` | Newer domain; renewal workflow evolving |
| Procurement (POs + Requests) | `procurement` | Low test density; requisition flow incomplete |
| Reporting (templates + schedules) | `extras` — reports | Designer API likely to change |
| Webhooks & Event Rules | `extras` — automation | Payload schema not frozen |
| SCIM Provisioning | `users` — API | Spec compliance gaps remain |
| Plugin System | infrastructure | Lifecycle hooks still being defined |

## How it is implemented

The source of truth is `core/features.py`:

```python
MODULE_MATURITY = {
    "subscriptions": BETA,
    "procurement": BETA,
}
```

App-level Beta modules get the banner automatically via `ObjectListView.get_context_data`. Features that share an app with stable code (reports, webhooks, SCIM) set `context['is_beta_module'] = True` in their individual list views.

Navigation badges are driven by `beta=True` on individual `MenuGroup` instances in `core/navigation/menu.py`.

## Promoting a module to Stable

1. Remove the app label from `MODULE_MATURITY` in `core/features.py`.
2. Remove `beta=True` from the corresponding `MenuGroup` in `core/navigation/menu.py`.
3. Remove any `context['is_beta_module'] = True` overrides in the relevant list views.
4. Update this document.
