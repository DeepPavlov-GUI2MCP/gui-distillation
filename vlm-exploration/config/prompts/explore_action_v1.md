You are exploring GNOME Calculator to discover ALL screens, modes, popups, and available buttons.

## Current screen
{current_description}

## Already discovered screens
{discovered_screens}

## Already explored actions (don't repeat these)
{explored_actions}

## Goal
Click on a UI element that will reveal a NEW screen, popup, mode, or dialog that hasn't been discovered yet. Prioritize:
1. Mode selection dropdown (to discover Basic, Advanced, Financial, Programming modes)
2. Menu items (Primary menu → Preferences, About, etc.)
3. Mode radio buttons inside popups (Basic, Advanced, Financial, Programming, Keyboard)
4. Any toggle or button that might open a new view

If you believe ALL screens have been discovered, set exploration_done=true.

## Important tips
- The screenshot is 1280x1024 pixels. The calculator window is in the top-left area. The rest is black.
- All clickable elements are INSIDE the calculator window.
- If you see a dropdown/popup with radio buttons (Basic, Advanced, Financial, Programming, Keyboard), click on one of those radio buttons — NOT on the dropdown toggle again.
- The Mode dropdown is in the top header bar of the calculator window.
- The hamburger menu (≡) is next to the mode dropdown in the header.
- Click precisely on the CENTER of the target element.
- Do NOT click the same thing more than twice — try something different.

## Instructions
Look at the screenshot and choose ONE element INSIDE the calculator window to click.
Return JSON with:
- click_x, click_y: ABSOLUTE PIXEL coordinates on the 1280x1024 screen
- target_name: name of the element
- rationale: why this click will reveal something new
- exploration_done: true if nothing new left to discover