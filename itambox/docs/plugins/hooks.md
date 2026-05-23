# ITAMbox Plugin Extension Hooks

This document details the developer hooks available to extend the web UI, menus, REST API, and GraphQL schema.

---

## 1. Template Injection

Plugins can inject custom HTML content into core detail page templates using `PluginTemplateContent`.

### Injection Points

Inherit from `itambox.plugins.views.PluginTemplateContent` and override any of the following methods to return HTML/safe string content:

- `head()`: Injects custom CSS/JS assets or metadata into the page's `<head>`.
- `navbar()`: Injects links or badges into the navigation header.
- `alerts()`: Injects custom warning/info banner alerts at the top of the main container.
- `buttons()`: Injects buttons into the page action button cluster.
- `left_panel()`: Injects a card/panel into the left detail page column.
- `right_panel()`: Injects a card/panel into the right detail page sidebar.
- `full_width_panel()`: Injects a card/panel spanning the full width at the bottom of the page.

### Example Template Injector

Create `itambox_esign/template_content.py`:

```python
from itambox.plugins.views import PluginTemplateContent

class AssetDocuSignContent(PluginTemplateContent):
    def head(self):
        return '<link rel="stylesheet" href="/static/itambox_esign/esign.css">'

    def left_panel(self):
        asset = self.context['object']
        # Return HTML card content
        return f"""
        <div class="card mb-3">
            <div class="card-header"><h3 class="card-title">DocuSign Status</h3></div>
            <div class="card-body">No pending envelopes for {asset.name}.</div>
        </div>
        """
```

Register this injector in `__init__.py`:

```python
registry.register_plugin_template_content('assets.asset', AssetDocuSignContent)
```

---

## 2. Sidebar Navigation

Plugins can register menu items and custom menus to display on the ITAMbox sidebar.

### Menus and Items

- `PluginNavigationItem`: Represents an individual navigation link.
  - `link` (str): Reversed URL name (e.g. `'plugins:itambox_esign:dashboard'`) or a raw URL path starting with `/`.
  - `link_text` (str): Visual text shown in the sidebar.
  - `permissions` (tuple): Required permissions to view.
  - `auth_required` (bool): If True, only logged-in users see it.
  - `staff_only` (bool): If True, only staff users see it.
- `PluginNavigationMenu`: Represents a parent dropdown menu grouping multiple items.
  - `label` (str): Display label of the dropdown menu.
  - `icon_class` (str): Material Design Icon class string (e.g. `'mdi mdi-file-sign'`).
  - `groups` (tuple): Nested tuples of `EsignGroup` classes.

### Example Menu Definition

Create `itambox_esign/navigation.py`:

```python
from itambox.plugins.navigation import PluginNavigationMenu, PluginNavigationItem

class EsignNavigationItem(PluginNavigationItem):
    link = 'plugins:itambox_esign:dashboard'
    link_text = 'DocuSign Envelopes'

class EsignGroup:
    label = 'E-Signature'
    items = (EsignNavigationItem,)

class EsignNavigationMenu(PluginNavigationMenu):
    label = 'DocuSign'
    icon_class = 'mdi mdi-file-sign'
    groups = (EsignGroup,)
```

Register this menu in `__init__.py`:

```python
registry.register_plugin_menu(EsignNavigationMenu)
```

---

## 3. Web UI Routing

To serve web pages, define views in `views.py` and register URL routes in `urls.py`.

### Example Routing

Create `itambox_esign/urls.py`:

```python
from django.urls import path
from .views import DocuSignDashboardView

app_name = 'itambox_esign'

urlpatterns = [
    path('dashboard/', DocuSignDashboardView.as_view(), name='dashboard'),
]
```

These views are dynamically mounted under `/plugins/itambox_esign/` and can be reversed in views/templates as `plugins:itambox_esign:dashboard`.

---

## 4. REST API Routing

Plugins can register Django REST Framework (DRF) viewsets directly into the central router.

### Example Viewset

Register in `__init__.py` using its dotted path string for lazy-loading:

```python
registry.register_plugin_viewset(
    'itambox_esign', 
    '', 
    'itambox_esign.api.views.DocuSignViewSet', 
    basename='itambox_esign'
)
```

This viewset is mounted at `/api/plugins/itambox_esign/`.

---

## 5. GraphQL Schema Extension

To extend the GraphQL schema, set `graphql_schema` in your config class and define standard Query/Mutation graphene ObjectTypes.

### Example Schema

Create `itambox_esign/graphql/schema.py`:

```python
import graphene

class DocuSignStatusType(graphene.ObjectType):
    status = graphene.String()

class Query(graphene.ObjectType):
    docusign_status = graphene.Field(DocuSignStatusType)

    def resolve_docusign_status(self, info):
        return DocuSignStatusType(status="Sent")
```

ITAMbox will dynamically merge this query alongside all core GraphQL queries at setup time.
