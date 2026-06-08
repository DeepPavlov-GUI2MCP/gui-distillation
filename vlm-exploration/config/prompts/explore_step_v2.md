You are exploring GNOME Calculator to discover ALL available functionality — every screen, mode, popup, dialog, menu, dropdown, and sub-menu.

## Screenshot info
The screenshot is 1280x1024 pixels. The calculator window is in the top-left area. The rest is black — ignore it.

## Already discovered screens
{discovered_screens}

## Actions already tried (DO NOT repeat)
{explored_actions}

## Your task
1. DESCRIBE what you see on the current screenshot:
   - screen_label: unique short name for this screen state
   - buttons: list ALL visible interactive elements (buttons, radio buttons, menu items, toggles, dropdowns, combo boxes). Use the visible label text.
   - description: 1-2 sentences about what functionality is available

2. DECIDE if this is a new screen:
   - Compare what you see to the already discovered screens list
   - is_new_screen: true if the current set of visible controls is DIFFERENT from all previously discovered screens

3. CHOOSE what to click next:
   - Click on something that will reveal NEW, undiscovered functionality
   - Prioritize: unopened dropdowns, mode radio buttons, menu items, combo boxes, any toggle not yet tried
   - click_x, click_y: ABSOLUTE PIXEL coordinates on the 1280x1024 screen
   - target_name: name of the element you're clicking
   - rationale: why you expect this to reveal something new

4. SET exploration_done=true ONLY when you are confident ALL of these have been found:
   - All calculator modes (Basic, Advanced, Financial, Programming, Keyboard)
   - Primary menu items (Preferences, About, Keyboard Shortcuts, Help, Number Format)
   - Any sub-menus or dialogs behind those items
   - Conversion dropdowns and unit selectors (if applicable)

## Rules
- ALL clickable elements are INSIDE the calculator window — never click outside it
- Do NOT click the same element twice — check the explored actions list
- If a dropdown/popup is already open, click items INSIDE it, not the dropdown toggle again
- Click precisely on the CENTER of the target element
- Be thorough — there may be hidden functionality behind combo boxes, conversion dropdowns, etc.