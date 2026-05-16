import os
import re

with open("bot.py", "r", encoding="utf-8") as f:
    content = f.read()

# The entire file is available in `content`
# Let's create a new folder `src`
os.makedirs("src", exist_ok=True)
os.makedirs("src/handlers", exist_ok=True)
os.makedirs("src/core", exist_ok=True)

# 1. We will need to copy everything else to src/ (config.py, db.py, llm.py, stt.py, etc.)
for file in ["config.py", "db.py", "llm.py", "stt.py", "reminders.py", "formatter.py", "keyboards.py", "requirements.txt", ".env.example"]:
    if os.path.exists(file):
        with open(file, "r", encoding="utf-8") as f:
            c = f.read()
        with open("src/" + file, "w", encoding="utf-8") as f:
            f.write(c)

# 2. Let's do a simple extraction for bot.py
def extract_section(start_marker, end_marker=None):
    start = content.find(start_marker)
    if start == -1: return ""
    if end_marker:
        end = content.find(end_marker, start + len(start_marker))
        if end == -1: end = len(content)
        return content[start:end]
    else:
        return content[start:]

imports_section = content[:content.find("\nlogger = ")]
logger_section = 'logger = logging.getLogger("secretary")\n\n'

helpers = extract_section("# ---- helpers -----------------------------------------------------------", "# ---- translate cache ---------------------------------------------------")
translate_cache = extract_section("# ---- translate cache ---------------------------------------------------", "# ---- Grok chat trigger ------------------------------------------------")
grok_trigger = extract_section("# ---- Grok chat trigger ------------------------------------------------", "# ---- command handlers --------------------------------------------------")
commands = extract_section("# ---- command handlers --------------------------------------------------", "# --- /get_N и /delete_N как динамические команды -----------------------")
text_handlers = extract_section("# --- /get_N и /delete_N как динамические команды -----------------------", "# ---- inline-button callbacks + edit flow ------------------------------")
callbacks_edit = extract_section("# ---- inline-button callbacks + edit flow ------------------------------", "# ---- main voice handler -----------------------------------------------")
voice_handler = extract_section("# ---- main voice handler -----------------------------------------------", "# ---- shared LLM + save + reminders pipeline ----------------------------")
pipeline = extract_section("# ---- shared LLM + save + reminders pipeline ----------------------------", "# ---- reminders scheduling ---------------------------------------------")
scheduling = extract_section("# ---- reminders scheduling ---------------------------------------------", "# ---- bootstrap ---------------------------------------------------------")
bootstrap = extract_section("# ---- bootstrap ---------------------------------------------------------")

# utils.py
utils_code = imports_section + logger_section + helpers + translate_cache + grok_trigger
with open("src/core/utils.py", "w", encoding="utf-8") as f:
    f.write(utils_code)

# Let's fix utils.py imports (it doesn't need everything but it's safe to keep)

# We will need some custom import fixing for the other files.
# It's actually easier to put edit_flow, processor, scheduling into core/ and handlers into handlers/
# But this requires a lot of manual import adjustments. 
# For example, handlers need utils, db, config, etc.

# Given the complexity, let's write a robust script that generates the complete files.
