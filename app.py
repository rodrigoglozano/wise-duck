# AI usage disclosure: Claude Code (Anthropic) was used throughout this file's
# development to speed up the implementation process, explain Flask/session/SQL
# concepts, help debug issues, and draft/review some of the comments seen here
# It also fixed the app.secret_key being hardcoded (now read from .env). 
# The feature set, route design, and the underlying design decisions (module/topic
# locking, session-only memory, the 50/day cap, the manual approval
# gate, etc.) are my own.

import os
import re
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

# Reads the .env file in this directory and copies its key=value lines
# into os.environ — must run before ask_ai.py's ask_ai() looks up
# GROQ_API_KEY, so this import stays at the top of the file.
load_dotenv()

from ask_ai import SYSTEM_PROMPT_TEMPLATE, ask_ai, discover_content, load_module_section
from db import count_todays_student_messages, get_db, init_db, log_exchange

# Names may contain letters (incl. accented ones), spaces, hyphens, and
# apostrophes (e.g. "Mary-Jane", "O'Brien", "José") — no digits, no markup.
NAME_RE = re.compile(r"^[^\W\d_]+([ '-][^\W\d_]+)*$", re.UNICODE)
MAX_NAME_LENGTH = 50

# Keeps a single question from being an unbounded wall of text before it's
# sent to the Groq API and logged to `messages`.
MAX_QUESTION_LENGTH = 2000

app = Flask(__name__)
# Flask signs the session cookie with this key so a client can't forge or
# tamper with it (e.g. write their own user_id in). Read from .env (via
# load_dotenv() above) rather than hardcoded, so it's never committed to
# version control.
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# Creates project.db and the users table the first time the app runs.
init_db()

# MODULES (valid module names) and TOPICS (module -> {letter: label}) are
# both derived from what's actually on disk under content/ — a professor
# adds/renames/reorders a topic by adding/renaming a file there, no code
# change needed. See discover_content() in ask_ai.py for the filename
# convention ("<letter> - <label>.md") this depends on.
MODULES, TOPICS = discover_content()

# Shown as the first "ai" bubble at the start of every quiz (both a brand
# new login and after /new) — never sent to load_module_section or built
# from module content, just a fixed greeting.
WELCOME_MESSAGE = (
    "Quack. I am Wise Duck, an experimental AI for rubberducking your way "
    "through university courses. Quack quack. Replies may not be accurate, "
    "prioritize your critical thinking and let me know if I've made a "
    "mistake! Also: your conversations are being recorded, so be nice to "
    "me :) Quack quack quack!"
)


# Decorator: redirect to /login if session has no logged-in user.
# Wrapping a route with @login_required means the route's own code
# never has to check this itself — the check happens before the view
# function runs at all.
def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect("/login")
        return view(*args, **kwargs)

    return wrapped_view


# Create a new account, stored with status='pending' by default.
# Only an approved account (done so by hand via sqlite3)
# can later log in and reach the quiz. See /login below.
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")

        if not (first_name and last_name and email and password):
            return render_template(
                "register.html",
                error="Please fill out every field.",
                first_name=first_name,
                last_name=last_name,
                email=email,
            )

        if len(first_name) > MAX_NAME_LENGTH or len(last_name) > MAX_NAME_LENGTH:
            return render_template(
                "register.html",
                error=f"First and last name must be {MAX_NAME_LENGTH} characters or fewer.",
                first_name=first_name,
                last_name=last_name,
                email=email,
            )

        if not (NAME_RE.match(first_name) and NAME_RE.match(last_name)):
            return render_template(
                "register.html",
                error="First and last name may only contain letters, spaces, hyphens, and apostrophes.",
                first_name=first_name,
                last_name=last_name,
                email=email,
            )

        if password != confirmation:
            return render_template(
                "register.html",
                error="Passwords don't match.",
                first_name=first_name,
                last_name=last_name,
                email=email,
            )

        # generate_password_hash salts and hashes the password with a
        # strong algorithm (scrypt by default) — the plaintext password
        # is never written to the database or kept around afterward.
        password_hash = generate_password_hash(password)

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (first_name, last_name, email, hash, status) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (first_name, last_name, email, password_hash),
            )
            db.commit()
        except Exception:
            # The `email` column has a UNIQUE constraint, so a duplicate
            # email raises sqlite3.IntegrityError, which lands here.
            return render_template(
                "register.html",
                error="An account with that email already exists.",
                first_name=first_name,
                last_name=last_name,
                email=email,
            )
        finally:
            db.close()

        # Redirect (rather than render_template directly) so refreshing
        # the confirmation page doesn't re-submit the registration form.
        return redirect("/registration-success")

    return render_template("register.html", error=None, first_name="", last_name="", email="")


# Confirmation page shown right after registering.
# No session/database access needed here — it's just static text
# telling the student their account was created as 'pending' and that
# they need to wait for a teaching assistant to approve it before they
# can log in.
@app.route("/registration-success")
def registration_success():
    return render_template("registration_success.html")


# Log a user in by storing their id in Flask's session.
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        db.close()

        # check_password_hash re-hashes the submitted password with the
        # same salt/algorithm stored in user["hash"] and compares the
        # result — the plaintext password is never compared directly.
        if user is None or not check_password_hash(user["hash"], password):
            return render_template("login.html", error="Invalid email and/or password.", email=email)

        if user["status"] == "rejected":
            return render_template(
                "login.html",
                error="Your account has been rejected. Please contact a teaching assistant for further information.",
                email=email,
            )

        if user["status"] == "pending":
            return render_template(
                "login.html",
                error="Your account is awaiting approval. Please check back later.",
                email=email,
            )

        session["user_id"] = user["id"]
        return redirect("/")

    return render_template("login.html", error=None, email="")


# Clear the session (log the user out) and send them back to /login.
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# Translate our session-shaped history into Groq's messages shape.
# `history` is a list of {"sent_by": "student"/"ai", "message": text}
# dicts (how we store it in session["history"]). Groq's chat API
# instead expects a list of {"role": "user"/"assistant", "content":
# text} dicts, one per turn — "assistant" (not Gemini's "model") for
# the AI's replies, and "content" (not "parts") holding the plain text.
# ask_ai() prepends the "system" entry itself, so this function only
# ever needs to produce user/assistant turns.
#
# Pure function: takes a list, returns a new list. No session access,
# no network calls, no side effects — easy to test on its own.
def format_history(history):
    messages = []
    for entry in history:
        role = "user" if entry["sent_by"] == "student" else "assistant"
        messages.append({"role": role, "content": entry["message"]})
    return messages


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    # session.get(...) returns None/[] on a brand-new login, since nothing
    # has been stored under these keys yet.
    locked_module = session.get("module")
    locked_topic = session.get("topic")
    history = session.get("history")
    if history is None:
        # First time this student has ever reached the quiz — greet them
        # before they've picked a module.
        history = [{"sent_by": "ai", "message": WELCOME_MESSAGE}]
        session["history"] = history
    response = None
    question = ""

    if request.method == "POST":
        # A reply is already in flight (see /respond below) — ignore any
        # extra submission until it resolves, rather than starting a
        # second overlapping AI call for the same conversation.
        if session.get("awaiting_reply"):
            return redirect("/")

        if locked_module is None:
            # First step: just pick a module. The topic choices depend on
            # which module this is, so topic can't be picked in this same 
            # step without JavaScript. Locks the module first, then shows that 
            # module's topics on the next render.
            selected_module = request.form.get("module")

            if selected_module in MODULES:
                session["module"] = selected_module
                locked_module = selected_module
            else:
                return render_template(
                    "index.html",
                    response="I'm a teapot.",
                    module_locked=False,
                    topic_locked=False,
                    topics={},
                    history=history,
                    question="",
                ), 418

        elif locked_topic is None:
            # Second step: pick a topic within the locked module and ask
            # the first question, in the same submission.
            selected_topic = request.form.get("topic")
            question = request.form.get("question", "")

            if selected_topic in TOPICS[locked_module] and question.strip():
                session["topic"] = selected_topic
                locked_topic = selected_topic
            elif selected_topic not in TOPICS[locked_module] and selected_topic is not None:
                return render_template(
                    "index.html",
                    response="I'm a teapot.",
                    module_locked=True,
                    topic_locked=False,
                    topics=TOPICS[locked_module],
                    history=history,
                    question=question,
                ), 418
            else:
                response = "Please pick a topic and type a question."
        else:
            # Module and topic already locked from earlier turns this
            # login. Next we validate that a question was typed.
            question = request.form.get("question", "")
            if not question.strip():
                response = "Please type a question."

        if response is None and len(question) > MAX_QUESTION_LENGTH:
            response = f"Please keep questions under {MAX_QUESTION_LENGTH} characters."

        # Only talk to the AI if we now have a locked module, a locked
        # topic, and a non-empty question — i.e. no validation message
        # was set above.
        if locked_module and locked_topic and question.strip() and response is None:
            student_id = session["user_id"]

            # Check the daily cap BEFORE calling ask_ai() at all — the
            # whole point of the cap is to stop spending API calls (and
            # eating into Groq's own free-tier quota) once a student
            # has hit their limit, so this has to happen first, not after
            # the request already went out.
            if count_todays_student_messages(student_id) >= 50:
                response = "You've reached today's limit of 50 questions. Please come back tomorrow!"
            else:
                # Show the student's message in the chat right away
                # instead of blocking this request on the (possibly
                # slow, possibly retried) call to the AI: append it to
                # history and hand off the actual API call to /respond
                # below. The page below renders immediately with the
                # question visible, and includes a <meta refresh> that
                # sends the browser straight to /respond — a second,
                # separate request that does the slow part.
                history.append({"sent_by": "student", "message": question})
                session["history"] = history
                session["awaiting_reply"] = True
                question = ""

    return render_template(
        "index.html",
        response=response,
        module_locked=locked_module is not None,
        topic_locked=locked_topic is not None,
        topics=TOPICS.get(locked_module, {}),
        history=history,
        question=question,
        awaiting_reply=session.get("awaiting_reply", False),
    )


# Actually call the AI for the question index() just queued.
# Reached via the <meta http-equiv="refresh"> in index.html, not
# JavaScript/AJAX — the browser does a normal second page load. This
# is what lets the student's own message show up on screen before the
# (slower) AI reply does, without needing any client-side scripting.
@app.route("/respond")
@login_required
def respond():
    if not session.get("awaiting_reply"):
        return redirect("/")

    locked_module = session["module"]
    locked_topic = session["topic"]
    history = session["history"]
    student_id = session["user_id"]

    module_text = load_module_section(locked_module, locked_topic)
    # Built once per turn from the (unchanging) module content — this
    # system_prompt never gets conversation turns mixed into it, no
    # matter how long the back-and-forth grows.
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(module_content=module_text)
    # index() just appended the student's message as the last entry in
    # history, so that's the question we're now answering.
    student_message = history[-1]["message"]
    groq_messages = format_history(history)

    try:
        ai_reply = ask_ai(system_prompt, groq_messages)
    except Exception as error:
        # Covers transient provider issues (rate limits, timeouts,
        # temporary outages) so a Groq hiccup shows a readable message
        # instead of crashing the request. Falling into `except` means
        # the `else` block below (and its log_exchange() call) never
        # runs — a failed attempt is never written to `messages` and
        # never counts against the cap.
        app.logger.error("ask_ai() failed: %r", error)
        ai_reply = "The AI is temporarily unavailable. Please try again in a moment."
    else:
        # Only reached if ask_ai() returned without raising: log both
        # halves of this real exchange to the database, tagged with this
        # student and the locked module.
        log_exchange(student_id, locked_module, student_message, ai_reply)

    history.append({"sent_by": "ai", "message": ai_reply})
    session["history"] = history
    session["awaiting_reply"] = False
    return redirect("/")


# Start a fresh quiz: forget the locked module and conversation so far
# (resetting history to just the welcome message), but leave
# session["user_id"] untouched so the student stays logged in.
@app.route("/new")
@login_required
def new_quiz():
    session.pop("module", None)
    session.pop("topic", None)
    session["history"] = [{"sent_by": "ai", "message": WELCOME_MESSAGE}]
    session.pop("awaiting_reply", None)
    return redirect("/")

# Display credits information.
@app.route("/credits")
def credits():
    return render_template("credits.html")


if __name__ == "__main__":
    app.run(debug=True)
