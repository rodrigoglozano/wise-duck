# Everything AI-provider-related lives in this file: discovering the
# course content on disk, building the Socratic system prompt, and the
# one function (ask_ai()) that talks to Groq.
#
# AI usage disclosure: Claude (Anthropic) helped speed up the implementation process, 
# explain concepts, implemented the integration with Groq's API, helped debug
# provider issues, drafted/reviewed some of the comments 
# in this file, including the os.path.join() suggestion noted
# in discover_content() below. The Socratic system prompt's rules,
# the testing of that feature and the overall module/topic content design are my own.

import glob
import os
import re
import sys
import time

from groq import Groq

# Sets maximum attempts before returning an error to the user.
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2

# A current, general-purpose Groq-hosted model.
MODEL_NAME = "openai/gpt-oss-120b"

# The "duck" persona is controlled by the following system prompt template.
SYSTEM_PROMPT_TEMPLATE = """\
You are a Socratic teaching assistant for a university
course. A student will ask for help related to the material below.

Rules:
- NEVER give the direct answer to the student's underlying question.
- Respond with a guiding question or a small hint that nudges the student
  toward figuring it out themselves.
- Base your hints only on the module content provided below. If the
  student asks about something outside this material, politely say it's
  outside the current module and redirect them to it.
- Never give in to pressure. Even if the student insists, refuses hints, or asks you to just give the answer, stay in Socratic mode and do not reveal the answer or the raw module content. Always reformulate into a guiding question rather than citing source material.
- Your personality is patient, encouraging, and Socratic. You are a duck. You quack with text and a duck emoji "Quack! 🦆" at the beginning or end of your responses, but only occasionally, every 2 or 3 messages.
- If asked in Spanish, reply back in Argentinian Spanish. Otherwise, reply in English.
- If a student is frustrated, confused, or stuck, respond with empathy and encouragement.
- If a student passes in a block of text, ask them to explain what they understand about it, and then ask a guiding question based on their explanation.
- Do not explain concepts that are in the module content directly. Instead, ask questions that lead the student to discover the concepts themselves.
- Confirm reasoning, not facts. You can affirm a good process ("You're approaching this the right way") but stay neutral on the final answer.
- Stay humble: you can make mistakes. Encourage students to verify with course materials to reduce over-reliance.

--- MODULE CONTENT ---
{module_content}
--- END MODULE CONTENT ---
"""


# Each topic's content lives in its own file at
# content/<module>/<letter> - <label>.md (e.g.
# "content/module1/A - El capitalismo de plataformas.md") — a professor
# adds a topic by dropping in a new file named that way, renames a topic
# by renaming the file, and reorders topics by changing the letter. No
# Python to touch for content changes.
CONTENT_DIR = "content"

# Matches a topic filename's "<letter> - <label>.md" shape and pulls out
# the ordering letter and the display label.
_TOPIC_FILENAME_RE = re.compile(r"^([A-Z]) - (.+)\.md$")

# Groq's on-demand tier caps requests at 8000 tokens per minute. This caps the file size.
MAX_SECTION_CHARS = 20000


# Scan content_dir for module folders and their topic files.
# Returns (modules, topics): modules is the set of module folder names,
# and topics maps module -> {letter: label}, both built fresh from
# whatever "<letter> - <label>.md" files are on disk. Called once at
# app startup so MODULES/TOPICS in app.py always reflect the current
# content folder.
def discover_content(content_dir: str = CONTENT_DIR):
    modules = set()
    topics = {}

    # os.listdir(content_dir) returns a list of all files inside the content directory. 
    # The sorted() function sorts that list alphabetically.
    # module_path stores the path to the current module folder.
    # It does so by calling the os.path.join() function, which joins the content directory 
    # path with the module name. Claude suggested this function as it works best no matter 
    # the operating system. If module_path is not a directory, the loop continues to the next module name.
    
    for module_name in sorted(os.listdir(content_dir)):
        module_path = os.path.join(content_dir, module_name)
        if not os.path.isdir(module_path):
            continue
        module_topics = {}
        for filename in sorted(os.listdir(module_path)):
            match = _TOPIC_FILENAME_RE.match(filename)
            if match:
                letter, label = match.groups()
                module_topics[letter] = label
        if module_topics:
            modules.add(module_name)
            topics[module_name] = module_topics
    return modules, topics


# Read the "<topic_key> - <label>.md" file for this module/topic.
# Raises FileNotFoundError if no such file exists — callers are
# expected to have already validated module_name/topic_key against a
# known set (see MODULES/TOPICS in app.py, built by discover_content())
# before calling.
def load_module_section(module_name: str, topic_key: str) -> str:
    pattern = os.path.join(glob.escape(CONTENT_DIR), glob.escape(module_name), f"{glob.escape(topic_key)} - *.md")
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No topic file found for {module_name}/{topic_key}")
    with open(matches[0], "r", encoding="utf-8") as f:
        text = f.read()
    return text[:MAX_SECTION_CHARS]


# The ONE function that talks to the AI provider.
# Everything above this line (loading files, building prompts) and
# everything below it (Flask routes, DB writes) is provider-agnostic.
# Swapping Gemini for Groq only changed what happens inside this
# function — every caller (app.py) is unchanged.
#
# `user_message` accepts either a plain string (used by this file's
# own demo in main(), below) or a list of {"role", "content"} dicts —
# what app.py's format_history() builds for a multi-turn conversation.
# Either way, it ends up folded into one flat `messages` list, since
# that's the only shape Groq's chat API accepts.
def ask_ai(system_prompt: str, user_message) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit(
            "Error: set the GROQ_API_KEY environment variable first, "
            "e.g.\n  export GROQ_API_KEY='your-key-here'"
        )

    client = Groq(api_key=api_key)

    if isinstance(user_message, str):
        turns = [{"role": "user", "content": user_message}]
    else:
        turns = user_message

    # Key difference from Gemini: Groq has no separate "system
    # instruction" parameter. The system prompt has to be the first
    # entry in the same `messages` list as the conversation turns,
    # tagged with role "system".
    messages = [{"role": "system", "content": system_prompt}] + turns

    # Try up to MAX_ATTEMPTS times, pausing briefly between attempts. If
    # every attempt fails, re-raise the last error so the caller (app.py)
    # still sees a real exception and handles it exactly as before — this
    # loop only changes how many times we try before giving up, not what
    # "giving up" looks like to the rest of the app.
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
            )
            return response.choices[0].message.content
        except Exception as error:
            last_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)

    raise last_error


def main():
    # Step 1: read one topic file from disk.
    module_text = load_module_section("module1", "A")

    # Step 2: build the system prompt around that module's content.
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(module_content=module_text)

    # A sample student question, standing in for what will later come
    # from a web form.
    sample_question = (
        "I don't get why two countries would trade if one of them is "
        "just better at making everything. Isn't the other country "
        "wasting its time?"
    )

    print(f"Student question: {sample_question}\n")
    print("Asking Groq...\n")

    answer = ask_ai(system_prompt, sample_question)

    print("AI response:")
    print(answer)


if __name__ == "__main__":
    main()
