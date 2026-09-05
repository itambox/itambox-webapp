# Kits, Components, And Stock

ITAMbox distinguishes reusable bundles, installed hardware components, accessories, and consumables so stock behavior matches the physical item being managed.

## Kits

A Kit is a reusable issue bundle made from supported items. It is useful for repeatable deployments such as a new-starter package or field-engineer set.

Kit checkout is atomic: the bundle should not be left half-issued when one required item cannot be allocated. Asset-Type kit items require a concrete asset. License items allocate an available seat. Stock-like items use quantity from the relevant stock location.

There is not one universal "return entire kit" action that perfectly reverses every allocation type. Return or check in the resulting allocations using the workflow appropriate to each item.

## Components

Components represent pooled hardware parts such as memory modules, drives, or processors. Component Stock records quantity at a location. Component Allocations record where quantity is installed or assigned.

Use components when individual units do not need their own Asset record but stock and installation history still matter.

## Accessories And Consumables

Accessories are reusable stock items that can be checked out and returned. Consumables represent stock that is consumed when issued rather than expected back as the same physical unit.

## Stock Accuracy

Always select the actual source location when a workflow asks for it. Avoid correcting inventory by editing totals without understanding the allocations that produced them.

If stock totals appear inconsistent, use the relevant reconciliation tools documented under [Management Commands](../operations/management-commands.md) and investigate the underlying assignments before changing production data.

See the Data Model pages for [Kits](../models/inventory/kit.md), [Components](../models/components/component.md), [Accessories](../models/inventory/accessory.md), and [Consumables](../models/inventory/consumable.md).
