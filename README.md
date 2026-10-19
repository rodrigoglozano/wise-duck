# Wise Duck 🦆

#### Video Demo: https://youtu.be/ce24VioyRmA

#### Description:

Wise Duck is a web app that acts like a CS50-style rubber duck for any
university course, although it was based on an international economics course. 
Instead of handing students the answer to whatever they're stuck on, it responds
the way a teaching assistant should: with a guiding question or a small hint that
nudges them toward figuring it out themselves, grounded only in the course's own material.

The reason for building this is that I did not know about AI tools suitable for university professors.
The closest one I knew about is CS50's own rubber duck, so I tried to replicate the way
it works with a few tweaks so that it is usable for social sciences. There is a second reason though,
and that is that a lot of students just use a general-purpose chatbot and copy whatever comes back,
without actually understanding the material which the shows on final exams. This behavior is to be expected
if trustworthy alternatives are non-existent. 

Such trustworthy alternatives likely exist; I do not doubt it. But it is also quite possible they are paid tools.
Since I teach at a public university where resources are often scant, I aimed at developing something that I could use within this context. Hopefully this is a first step in the right direction. Given the project's scale and complexity I did have to use AI to circumnavigate some hurdles. Specifically I used Anthropic's Claude Code. Please check the code's commentaries for more information on this matter.

Lastly, Wise Duck is deliberately restricted: it only ever has access to the specific topic the student selected, 
and its system prompt forbids it from ever stating the underlying answer directly — no matter how the student 
phrases the request or how much they insist. This is particularly important so as not to disclose the source
material.

### How it works

A student registers with their first name, last name, email, and a password
(hashed with `werkzeug.security`). New accounts start with status `pending` 
and can't log in yet. Someone, like a teaching assistant, has to manually 
change the account to `approved` directly in the database using SQL commands. 
This is the main defense against bots or strangers running up the app's free-tier AI
quota. It is also a cumbersome, but fixing it would require me to develop some sort
of panel to manage accounts which was a bit out of scope.

Once approved and logged in, the student picks a **module** and then a **topic** within that module.
The topics are associated to markdown files which are read off disk and used for the AI's prompt;
The module and topic stay locked for the rest of that login session, 
so the app never sends more course content to the AI than the one topic actually being discussed. 
The student then types a question and the duck answers with a hint, never the answer itself.

The conversation renders as a chat thread. Submitting a question appends it
to the thread immediately and redirects to `/respond`. This lets the student's own message show up on
screen right away instead of the whole page hanging while the AI "thinks."

Conversation memory lives only in Flask's signed session cookie for the
current login. That's a deliberate choice: closing the browser ends the conversation and it keeps every API call's context limited to what's actually been exchanged in this session. 

The database's `messages` table still logs every exchange, but purely to compute each student's daily question count (a hard cap of 50 questions per day).

### AI provider

The app talks to Groq's free-tier API (currently `openai/gpt-oss-120b`).
Every provider call goes through a single function, `ask_ai()` in
`ask_ai.py` — if Groq's catalog changes or the project switches providers
again, that one function needs to change.

### File structure

- `app.py` — all Flask routes: registration, login/logout, the module/topic
  picker, the chat loop, and the daily-cap check.
- `ask_ai.py` — everything AI-related is here: the Socratic
  system prompt, `discover_content()` (scans `content/` so adding a new
  topic is just adding a file, no code change), `load_module_section()`,
  and `ask_ai()` itself.
- `db.py` — a thin `sqlite3` wrapper: connection helper, table creation,
  the daily message count query, and the exchange logger.
- `content/module{1,2,3}/` — one markdown file per topic, named
  `<letter> - <label>.md` (e.g. `A - ... .md`). This naming convention
  is what `discover_content()` parses to build the module/topic pickers
  automatically, so anyone attempting to use their own files needs to
  respect this convention. **Note:** the real material I teach with
  isn't fully mine to redistribute, and separately, as the instructor,
  I don't want excerpts of it circulating as a substitute for the
  actual source material students should be studying from — both
  reasons apply at once. So the files submitted here are placeholder
  sample topics with the same structure, only so the app can be run
  and graded end to end — `discover_content()` and
  `load_module_section()` work identically regardless of which content
  is dropped into these folders.
- `templates/layout.html` — the base template every other page extends,
  as learned during the course. It sets up the `<head>` (page title,
  `style.css`) and renders a "Credits" link in the footer on every page,
  so that markup only has to be written once.
- `templates/login.html` — the login form (email + password) and the
  error banner shown if credentials are wrong or the account is still
  `pending`/`rejected`, plus a link to `register.html` for new students.
- `templates/register.html` — the registration form (first name, last
  name, email, password, password confirmation). The name fields use an
  HTML `pattern` attribute as a first line of client-side validation
  (letters, spaces, hyphens, apostrophes only), mirroring the same regex
  enforced server-side in `app.py` — client-side alone isn't trustworthy,
  since a request can always be sent without going through the form.
- `templates/registration_success.html` — a static confirmation page
  shown right after registering, telling the student their account is
  `pending` and that they need a teaching assistant to approve it before
  logging in.
- `templates/index.html` — the actual quiz page: renders the chat thread
  bubble by bubble, the module/topic picker (shown only until each is
  locked for the session), the question textarea, and the
  `<meta http-equiv="refresh">` tag that silently sends the browser to
  `/respond` while an AI reply is pending.
- `templates/credits.html` — acknowledges CS50 course staff, the duck
  GIF artist, and this project's use of AI assistance (see below).
- `static/` — the duck GIFs used across pages (idle, walking, flying,
  etc., swapped depending on `awaiting_reply`) and `style.css`, which
  holds every page's styling (the chat bubbles, the auth-page cards, the
  module/topic picker, etc.).
- `requirements.txt` — the project's Python dependencies (`flask`,
  `groq`, `python-dotenv`, `werkzeug`), so the app can be set up with a
  single `pip install -r requirements.txt`.
- `.env` — holds `GROQ_API_KEY` and `FLASK_SECRET_KEY`; excluded from
  version control via `.gitignore` and never sent to the client.

### Design choices

- **Sample content instead of the real material.** See the
  `content/module{1,2,3}/` entry under "File structure" above for why.
- **Topic-level, not module-level, content loading.** Only the selected
  topic's single markdown file is sent to the AI per question, not the
  whole module and never all three modules — this keeps every request
  small and cheap at the cost of having the student be more specific
  with what they want to discuss.
- **Session-only conversation memory.** Rejected re-loading history from
  the database on login, since the spec's goal was to keep token usage (and
  provider load) down, not to let students resume old conversations.
- **No admin UI.** Approving students requires a manual `sqlite3` `UPDATE`
  command rather than a professor role with its own login — intentionally
  out of scope for a first version.
- **Socratic system prompt with explicit anti-jailbreak language.** The
  prompt tells the model to never give in even if a student insists,
  refuses hints, or asks directly for the answer. This was tested manually by
  trying to argue the duck into just answering.

### How to run it locally

This app calls Groq's API, so running it live needs a Groq API key —
free at [console.groq.com](https://console.groq.com) (sign-up takes a
minute, no credit card). I'm not including my own key in this
submission, since API keys shouldn't be shared even privately.

```
pip install -r requirements.txt
echo "GROQ_API_KEY=your-key-here" >> .env
echo "FLASK_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> .env
python3 app.py
```

Then visit `http://127.0.0.1:5000`. Register an account, then approve it
by hand:

```
sqlite3 project.db "UPDATE users SET status = 'approved' WHERE email = 'you@example.com';"
```

### Out of scope / Stretch goals

Fine-tuning a model on the course material, password reset flows, and
content were all explicitly out of scope for this version. A
 professor/admin role (to approve students and see which
topics they struggle with most, from inside the app instead of 
`sqlite3`) are possible next steps.

## Credits

The Duck GIF is from **Skagon Game**r https://itch.io/profile/skagon-gamer"


— Rodrigo Lozano
