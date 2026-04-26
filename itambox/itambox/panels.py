# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.

"""
Panel layout system for ObjectDetailView.

Defines a declarative grid layout where views specify panels as a
list-of-lists-of-lists, matching NetBox's pattern:

    layout = (
        (  # Row 1
            (Panel(...), Panel(...)),  # Column 1 (nested panels stacked)
            (Panel(...),),             # Column 2
        ),
        (  # Row 2 (full width)
            (Panel(...),),
        ),
    )

Each Panel is rendered by looking for a named template block
(panel_{name}) defined in the subclass template.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Panel:
    """A declarative panel in a detail view layout."""

    name: str
    label: str = ''
    description: str = ''
    fields: list = field(default_factory=list)
    template_name: str = 'generic/includes/panel_wrapper.html'
    position: str = 'left'
    extra_context: dict = field(default_factory=dict)

    @property
    def display_label(self):
        return self.label or self.name.replace('_', ' ').title()
