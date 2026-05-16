import os
import re

with open("bot.py", "r", encoding="utf-8") as f:
    content = f.read()

# Make directory
os.makedirs("handlers", exist_ok=True)
os.makedirs("core", exist_ok=True)
with open("handlers/__init__.py", "w") as f:
    f.write("")
with open("core/__init__.py", "w") as f:
    f.write("")

# We will define the split boundaries by finding headers
sections = {}
headers = [
    "# ---- helpers -----------------------------------------------------------",
    "# ---- translate cache ---------------------------------------------------",
    "# ---- Grok chat trigger ------------------------------------------------",
    "# ---- command handlers --------------------------------------------------",
    "# --- /get_N и /delete_N как динамические команды -----------------------",
    "# ---- inline-button callbacks + edit flow ------------------------------",
    "# ---- main voice handler -----------------------------------------------",
    "# ---- shared LLM + save + reminders pipeline ----------------------------",
    "# ---- reminders scheduling ---------------------------------------------",
    "# ---- bootstrap ---------------------------------------------------------"
]

indices = [content.find(h) for h in headers]
indices.append(len(content))

parts = []
for i in range(len(headers)):
    parts.append((headers[i], content[indices[i]:indices[i+1]]))

header_content = content[:indices[0]]

# utils.py
utils_content = header_content + parts[0][1] + parts[1][1] + parts[2][1]
utils_content = utils_content.replace('logger = logging.getLogger("secretary")', 'logger = logging.getLogger("secretary")\n\n')

# core/edit_flow.py
edit_flow_content = header_content + parts[5][1]
# We'll need to clean it up later

print("Done generating sections. We will now do manual cleanup via another script or manually.")

