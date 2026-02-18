---
name: teams-sender
description: Send a message to someone on Microsoft Teams. Use when the user wants to message a person or channel on Teams. Requires browser automation via Playwright.
---

# Teams Sender

Send a message to a person on Microsoft Teams using Playwright browser automation.

## Workflow

1. Navigate to https://teams.microsoft.com
2. Wait 5 seconds for the page to load
3. Take a browser_snapshot to see the current state
4. Click on the "Chat" icon in the left sidebar (or press Ctrl+Shift+2)
5. Wait 2 seconds
6. Use the search box at the top to find the recipient by name
7. Wait for search results to appear (2 seconds)
8. Take a browser_snapshot — read the search results carefully
9. **MANDATORY CONFIRMATION** — call ask_user with:
   - The recipient's full name, email address, and job title from the search results
   - The exact message text you will send
   - If MULTIPLE people match, list ALL of them and ask the user to pick
   - Format:
     "Confirm Teams message:
      To: [Full Name] ([email@domain.com]) — [Job Title]
      Message: [exact message text]

      Reply YES to send, or NO to cancel."
10. WAIT for the user's response. If they say NO or anything other than YES, abort immediately.
11. Only after YES: click on the correct person from the search results
12. Wait 2 seconds for the chat to open
13. Take a browser_snapshot to find the compose/message box
14. Type the message using browser_type
15. Click the Send button (browser_click on Send)
16. Confirm the message was sent by taking a final browser_snapshot

## Rules

- NEVER send a message without calling ask_user first and receiving YES.
- NEVER guess which person to message if multiple results appear — list all and ask.
- ALWAYS include the recipient's email address in the confirmation prompt. The email is visible in Teams search results.
- If Teams shows a login page, STOP and report that the session has expired.
- Keep messages professional and concise.
- Do NOT modify or delete any existing messages.
