# Dashboard

The **Dashboard** is the default landing page of ITAMbox. It provides a highly customizable, widget-based overview of your IT asset management database, including key metrics, analytics, and shortcuts to common tasks.

---

## Key Features

- **Dynamic Widgets**: Choose from a variety of widgets like charts, lists, and summary cards.
- **Customizable Layout**: Rearrange and resize widgets to suit your needs.
- **Multi-Dashboard Support**: Create, rename, delete, and switch between multiple distinct dashboards.
- **Role & Tenant Filtering**: Dashboard data is automatically scoped to the user's active tenant, ensuring data segregation.

---

## Locking & Unlocking the Layout

To prevent accidental changes, the dashboard layout is **Locked** by default. To make any structural changes (adding, reordering, resizing, or removing widgets), you must toggle the edit mode:

1. **Unlock the Layout**: Click the **Unlock** button in the top-right dashboard toolbar. This reveals the layout customization options and widget edit controls.
2. **Lock the Layout**: Once you have completed your customizations, click the **Lock** button in the toolbar to freeze the layout and hide edit controls.

---

## Managing Widgets

> [!IMPORTANT]
> The layout must be **Unlocked** to add, reorder, resize, or remove widgets.

### Adding a Widget
1. Ensure the dashboard is **Unlocked**.
2. Click the **+ Add Widget** button in the toolbar.
3. Select the widget type you want to add (e.g., *Asset Status Chart*, *Recent Changes*, *Expiration Alerts*).
4. The widget will be appended to the bottom of the active dashboard layout.

### Configuring a Widget
1. Click the **Cog (Gear)** icon on the top-right of any widget (this is available in both locked and unlocked modes).
2. Adjust the widget-specific settings in the modal (e.g., custom title, limit of items, date ranges, or categories to filter).
3. Click **Save** to apply the configuration.

### Reordering / Resizing Widgets
1. Ensure the dashboard is **Unlocked**.
2. **Reorder**: Click and hold a widget's header, then drag it to your desired position.
3. **Resize**: Drag the edges/corners of a widget to adjust its height and width.
4. **Save Layout**: Click the **Save Layout** button in the toolbar to persist your custom positions.

### Removing a Widget
1. Ensure the dashboard is **Unlocked**.
2. Click the **Close / Trash** icon on the top-right of the widget to remove it from your layout.

---

## Managing Dashboards

You can maintain multiple dashboards for different tracking purposes (e.g., one for *Hardware Inventory*, another for *SaaS Licenses*).

- **Create Dashboard**: Click the dropdown menu next to the dashboard title and select **Create New Dashboard**.
- **Set Default**: Check the **Set as Default** option in the manage menu to make a dashboard load automatically upon login.
- **Reset Layout**: If you want to discard your custom layout changes and revert to the default preset, click **Reset** (the red refresh icon) in the dashboard toolbar.
