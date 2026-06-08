You are exploring {app_name} to discover ALL available functionality — every screen, mode, popup, dialog, menu, dropdown, and sub-menu.

## Screenshot info
The screenshot is {screen_width}x{screen_height} pixels. The application window is in the top-left area. The rest is black — ignore it.

## Already discovered screens
{discovered_screens}

## Actions already taken (DO NOT repeat the same click from the same screen)
{explored_actions}

## Your task

Fill in ALL fields in the exact order below.

### Stage 1 — OBSERVE the screenshot

**elements**: list EVERY visible UI element. For each one provide:
- **name**: visible label or accessible name
- **type**: one of: `button`, `input`, `dropdown`, `toggle`, `radio`, `menu_item`, `label`, `link`, `other`
- **value**: current state — display text for labels, selected item for dropdowns, "on"/"off" for toggles, "selected"/"unselected" for radios, null for buttons/menu_items
- **enabled**: false only if greyed out

**screen_label**: unique short name for this screen state.
**description**: 1-2 sentences about what is visible.
**is_new_screen**: true if this screen differs from all previously discovered screens.

### Stage 2 — REASON before acting

**reasoning**: Think step by step. You MUST address ALL of these:
1. Which screens/modes/dialogs have already been discovered? (check the list above)
2. Which top-level areas are still UNEXPLORED? (modes, menu items, dialogs you haven't opened)
3. Is there currently an open popup/dialog/dropdown that should be closed first to continue exploring?
4. Have ALL discoverable areas been visited? Only if YES — set exploration_done=true.

**exploration_done**: true ONLY when your reasoning above concludes there is genuinely nothing left to explore. If ANY visible menu item, dialog, mode, or interactive area has not been opened — this MUST be false.

### Stage 3 — ACT (skip if exploration_done is true)

**click_x**, **click_y**: absolute pixel coordinates of the CENTER of the target element.
**target_name**: name of the element to click.

## Strategy
- First discover all top-level modes or views the application offers
- Then open menus and visit each menu item
- Explore dialogs, settings, and sub-screens
- If stuck in a dialog, close it (click X or press outside) and continue
- Prioritize breadth over depth — discover new areas before going deep into one

## Rules
- ALL clicks must be INSIDE the application window
- Click precisely on the CENTER of the target element
- Changing a setting value (e.g. switching a dropdown option) is NOT a new screen — same dialog, different value
