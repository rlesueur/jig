# Jig UI review: a bigger, always-present Jig, and a friendlier work view

Phase 1, design only. Nothing in `jig/web/` or `avatar/` was changed, and nothing here is committed.

The mockups and contact sheets are in
`C:\Users\you\.cursor\projects\c-Users-robyn-VideoAvatar\assets\jig-ui-review\` (the file index is at the end). They use the real
`style.css` tokens, Nunito, and the approved `<jig-avatar>` component, posed on its manual clock with a fixed seed so every
render shows the same frame. The copy is British English and doesn't name any model.

**Recommendation: Direction A, "Side by side"**, borrowing two touches from the others: the speech-bubble voice from B and
the full-size celebration from C. The reasons are under [Recommendation](#recommendation).

## 1. What Jig looks like today

I ran a spare Jig (port 8820, fresh data folder, the local model on 8080, Docker sandbox on) and a second spare in set-up
mode (port 8821). Both ran headless, and both are turned off again. I didn't touch 8080, 8766, 8770 or 8792. There are 82
screenshots in `current/`, and `sheet-current.png` summarises them.

What I found:

1. **Jig is a sprite once you start talking.** The avatar is 220px on the empty screen, then drops to 76px (56px on a
   phone) as soon as there's one message. Settings, the activity drawer, the confirmation dialogues and sign-in have no
   live Jig at all. "Jig is off" uses a static face picture, even though the component is already loaded and could show
   Jig asleep.
2. **During real work, Jig mostly shows "Thinking…" and "Talking…".** I asked Jig to write and test a small Python module
   in the sandbox. It ran four commands, each one approved. The coding pose was only on screen while a command was
   actually running, which was too brief for my capture to catch even polling every 120 ms (`06-coding-live-*`). The
   pose that best explains the job is the one people almost never see.
3. **Coding shows up as a wall of narration.** The reply streams the model's running commentary ("Now let me run the
   tests", "Both failures are…") and then piles up a dashed "You said yes" card for every command (`07-coding-done-*`).
   Turning on "Show Jig's working" adds raw tool names (`list_files`, `run_command`) and the Sentinel's reasoning
   (`08-*`). It's accurate, but it reads like a log.
4. **The status bar contradicts what's happening.** While Jig is coding in the chat, the bar under the conversation says
   "Nothing on the go in the background" (`06-coding-live-*`), because it only counts background jobs.
5. **Bug: the page grows instead of scrolling.** `body` has `min-height`, not a fixed height, so `.chat-log` never
   scrolls by itself. Once the conversation is taller than the window, the whole document scrolls, and the header, Jig,
   the status bar and the message box scroll out of view. I measured the document at 1258px tall in an 820px window
   (`07b-*`, `09-background-job-narrow-*`). Every direction below relies on this being fixed.
6. Smaller things:
   - The approval card is clear and good. Keep its structure: it appears in every direction.
   - "Jig is off" tells people to "run jig serve", which won't mean much to non-technical users.
   - The native window's background colour (`#FBFAF7` in `desktop.py`) is neither theme's `--bg`, so the window can flash
     before the page paints, which is most visible in dark mode.

## 2. Rules every direction follows

These come from the avatar constraints and from accessibility. Any direction that gets built should keep them.

- **One Jig on screen at a time.** When a sheet, spotlight or drawer brings Jig forward, the resting Jig hides. Two Jigs
  look like two agents.
- **Never smaller than 112px.** The component switches to icon framing below 110px, and the face stops reading as
  friendly. Icons (tray, favicon, notifications) are the only exception.
- **The caption is the text equivalent.** Every pose comes with one plain sentence in Jig's voice (`role="status"`,
  `aria-live="polite"`). The component's own `role="img"` label stays, but it isn't live, so screen readers hear each
  change once. Example captions: "Reading walking guides for Bath…", "2 tests failed, so I'm fixing them now.", "Done!
  It's in your calendar." The caption must never depend on the pose to make sense.
- **Poses hold long enough to be seen.** A working pose is held for at least two seconds, and the next working pose
  takes over from it directly, without dropping to "thinking" between tool calls. "Thinking" is for genuine reasoning,
  such as working out why a test failed.
- **Questions win.** `approval` beats every other state, as the avatar README already specifies. The question card
  always stays fully visible, buttons included, at every size. A, B and C each step Jig back a little to make room.
- **Focus.** A new question doesn't steal focus while someone is typing. It announces "Jig has a question" and moves focus
  to the card's heading on the next Tab. Only C's spotlight is modal: it traps focus, and Esc means "Not now", not "No".
  "Show the steps" and each step's toggle are buttons with `aria-expanded`.
- **Colour is never the only signal.** Results carry a ✓ or ✕ and the words "passed" or "failed". The diff has −/+
  markers, strikethrough on removed lines, and "Before"/"After" headings. The test meter has a text label next to it and
  an `aria-label` ("6 of 8 tests passing").
- **Reduced motion.** The component already stops dancing, hopping, squash and stretch, and confetti, and cuts movement
  to about 15%. On top of that, the layout should make sheets, drawers and spotlights appear instantly instead of
  sliding or zooming, replace the success celebration with the happy pose and the caption, and keep the C spotlight for
  questions only (no celebration spotlight). A future "Jig's movement: follow my system, calm, lively" choice under
  Appearance would fit naturally, but it's optional.
- **Contrast.** I measured all the new text pairings in both themes (`design/ui-review/contrast-report.json`, produced by
  `scripts/contrast.mjs`). The lowest are 5.77:1 in light and 5.71:1 in dark, so everything clears AA. Two items sit on
  gradients and weren't measured automatically: the corner tip and the set-up step list. Both use `--muted` on a surface
  tint, a pairing `style.css` already uses.

## 3. Direction A: Side by side

Jig gets its own corner beside the conversation, on every screen.

- **Layout.** On windows 960px wide or more, a column on the left (`clamp(300px, 28vw, 360px)`) holds Jig at
  `clamp(230px, 22vw, 280px)`, the speech-bubble caption, and a context area under it. The conversation sits on the
  right (max 760px), with the message box at the bottom.
- **The context area changes with what Jig is doing.**
  - Idle: "On the go" and "Coming up" (a glance at the activity drawer), plus a tip.
  - Working: the work summary.
  - Asking: the question card, held by Jig, with a notch pointing back at it.
  - Done: the result card.
  - Set-up: the steps.
  - Settings: a sentence about the current section.
  - Off: Jig asleep (the paused pose).
  The Pause and "What Jig's up to" buttons move from the status bar to the foot of the corner. That retires the
  misleading "Nothing on the go" line.
- **Narrow (under 960px, including small native windows).** The corner becomes a band across the top: Jig at 124px on a
  phone, 140px in a small window, with the caption beside it. The empty home keeps a big centred Jig (230px). Questions
  and the step list open as a bottom sheet with Jig, 128px, peeking over its top edge. The band hides while a sheet is
  open, so there's only one Jig.
- **While asking on desktop,** Jig steps back to 196px so the whole card and its buttons fit, and the conversation shows
  a small "Waiting for your answer next to Jig" pill.
- **Work view.** The corner holds the one-line status, the test meter and "Show the steps". Opening the steps switches the
  right-hand column to a Steps tab, which gives the before and after enough width to sit side by side. Jig keeps working
  in the corner.
- **Strengths.**
  - Jig is the same size in the same place on every screen, Settings included.
  - The conversation stays a normal, readable chat.
  - Questions, progress and results all have one obvious home, next to the character delivering them.
  - It fits the native window's default 1200×860 comfortably.
- **Risks.**
  - It takes about a third of the width, so long replies wrap sooner. 760px is still roomy.
  - It needs a clear narrow-width layout, which is covered above.

Mockups: `direction-a/`, `sheet-a-overview.png`, `sheet-a-work.png`.

## 4. Direction B: Jig talks to you

Jig stands on a stage by the message box and speaks in a big bubble.

- **Layout.**
  - Jig is 230px at the bottom left, just above the message box, on a soft floor shadow. A large speech bubble to its
    right holds whatever Jig is saying now: the live reply, the question card, the work status or the result.
  - Earlier turns scroll above in a quieter transcript.
  - The empty home and first-run screens put Jig at 300px, with the greeting or set-up form inside the bubble, so set-up
    reads as Jig asking you questions.
  - In Settings, Jig and a bubble sit above the section list and explain what's on screen.
- **Asking.** The bubble becomes the question, with an amber border, and the transcript fades back. On a phone, the card
  fills the width and Jig, at 150px, stands under its tail.
- **Work view.** The bubble holds the status, the meter and "Show the steps". The steps open as a panel above the stage, and
  Jig steps back to 150px with a one-line bubble.
- **Scale.** Height drives the stage: `clamp(124px, 30vh, 230px)`. That's 124px on a phone, 150px while asking or with the
  steps open, and 300px for the hero screens (210px on a phone).
- **Strengths.**
  - It's the most character-led direction, and it's very clear who's speaking.
  - Set-up and questions feel like a conversation with Jig rather than forms.
- **Risks.**
  - Long answers don't suit a bubble. They need "Read all" or have to move up into the transcript, and either way
    reading back through a conversation becomes two-tier.
  - The stage costs vertical space, which the native window's 520px minimum height makes tight.
  - People who use Jig all day may find the visual-novel framing gets old.

Mockups: `direction-b/`, `sheet-b-overview.png`, `sheet-b-work.png`.

## 5. Direction C: Jig's home

Jig gets a big hello, a perch beside the message box, and a spotlight for the moments that matter.

- **Layout.**
  - The empty home is a hero: Jig at 300px, "Good evening! What shall we do?", a wide message box, examples, and "On
    the go" and "Coming up".
  - Once you start talking, Jig perches beside the message box: 200px in the right-hand margin on desktop, or 116px
    sitting on the box's top-right corner on a phone or small window. Its caption appears in a little bubble above it.
- **Moments.** A question or a finished job brings Jig forward into a spotlight: 230px, or 300px to celebrate, holding the
  card, with everything else dimmed. The celebration goes away by itself, and Esc dismisses it too. Settings keeps the
  perched Jig with a short tip.
- **Work view.** The perch bubble holds the status, the meter and "Show the steps". The steps open in a workbench drawer
  on the right with Jig at 150px at the top. It's full-screen on a phone, and the change is shown stacked.
- **Strengths.**
  - It has the warmest first impression.
  - The spotlight makes questions impossible to miss.
  - It leaves the conversation at full width.
- **Risks.**
  - A modal spotlight interrupts. If Jig asks several questions in a row, it will feel naggy.
  - The perch depends on a wide enough margin, so it can cover content on mid-sized windows.
  - Jig's size changes the most from screen to screen (300, then 200, then 230), which is the opposite of "consistent".

Mockups: `direction-c/`, `sheet-c-overview.png`, `sheet-c-work.png`.

## 6. The work view, in every direction

The goal is to show more of what Jig does when it codes, without it ever looking like a terminal by default. The detail
is in three levels:

| Level | What you see |
| --- | --- |
| 0 (default) | One friendly line ("2 tests failed, so I'm fixing them now."), a pass/fail meter while there are test results, and "Show the steps", with a count of steps so far. |
| 1 | Each step in plain words, with a ✓ when done: "Looked at your files" (and which ones), "Ran the tests" with passed/failed pills, "Found the problem" with one sentence, "Changed budget.py" with how many lines, and "Running the tests again…" for the current step. |
| 2 (per step) | Ran the tests: the command, labelled "What I ran, in my sandbox", a one-line explanation, and each test with ✓/✕ and the reason it failed ("'£12.50' couldn't be read as a number"); "Show the full output" goes one level deeper still. Changed a file: a plain sentence about what changed, then Before and After with line numbers, side by side when there's room and stacked on a phone. |

How the poses map to the steps:

| What Jig is doing | Pose |
| --- | --- |
| Reading or listing files | `working` + `browsing` |
| Writing code, running commands or tests | `working` + `coding` |
| Working out why something failed | `thinking` |
| All tests pass or the job is done | `success` |
| A command needs an OK | `approval` |
| Something is blocked | `error` |

After the job, the answered command cards collapse into one line in the steps ("You OK'd 4 commands"), instead of
stacking up under the reply.

**What this needs from the runtime.** Today `tool.start` carries only the tool's name, category, variant and effect, and
`tool.end` carries only `ok`. The work view needs a short, redacted result summary per call:

- the paths read or written;
- for commands, the command (already shown on its approval card), the exit code and the last few lines of output;
- test counts and failing test names, parsed from the pytest or unittest summary line;
- for file writes in the sandbox, the before and after.

The step sentences should come from fixed templates for each tool and result rather than from the model, so they stay
short and trustworthy. The model's running commentary would then move into the steps, and the final reply could be a
short summary.

## Recommendation

**Build A, "Side by side".** It's the only direction that keeps Jig large, in one place and at one size on every screen,
Settings and set-up included, without making the conversation harder to read or interrupting people. That consistency is
what the request asks for.

- It gives questions, progress, results and set-up an obvious home next to the character that is delivering them, which
  is what makes Jig feel like a companion rather than decoration.
- It works in the native window at its default size, and its narrow layout (band plus bottom sheet with Jig peeking) is
  simple to follow.
- From B, keep the speech-bubble caption as Jig's voice, as A's mockups already do.
- From C, keep the full-size celebration for finished jobs, but inside the corner or band, not as a modal, and only when
  motion is allowed.

Before or alongside A, fix the page-grows bug (finding 5), make tool poses hold long enough to be seen (finding 2), and
add the runtime summaries the work view needs.

## As built: where the stop button and the runaway notice go

Direction A is built (`jig/web/app.js`, `work.js`, `style.css`). Two pieces of work land in it separately, and the layout
leaves them their place:

- **Stopping a reply.** There is one Stop for a reply, "Stop the reply" in the status row under Jig, so it is always in
  the same place; the work line has none. While Jig holds a question, the row keeps only that button. On a narrow window
  the band keeps it beside the work line (the row's status text, Pause and "What Jig's up to" step aside while the work
  line is showing), and the sheet keeps it too.
- **"Jig stopped this reply because it was repeating itself".** This belongs to the reply, so it sits under the reply in
  the conversation with Continue anyway and Try again, like any other note on a reply. The corner's work line ends as
  stopped (the work view has a `stopped` status for it); no second Jig and no pop-up.

## Files

All PNGs are in `C:\Users\you\.cursor\projects\c-Users-robyn-VideoAvatar\assets\jig-ui-review\`.

| File | What it is |
| --- | --- |
| `sheet-compare.png` | The three directions side by side, for the same six moments, desktop and phone. |
| `sheet-a-overview.png`, `sheet-b-overview.png`, `sheet-c-overview.png` | One contact sheet per direction: every desktop screen in light, the key ones in dark, and the phone screens. |
| `sheet-a-work.png`, `sheet-b-work.png`, `sheet-c-work.png` | The coding work view per direction: default, steps with the change, and test results, in light, dark, on a phone and in a small window. |
| `sheet-current.png` | Jig today. |
| `direction-{a,b,c}/{dir}-{screen}-{size}-{theme}.png` | 35 PNGs per direction. Screens: `home`, `chat`, `approval`, `work`, `work-open`, `work-tests`, `success`, `setup`, `settings`, `off`. Sizes: `desktop` (1200×800), `narrow` (390×844 at 2×), `compact` (560×720, a small native window). |
| `current/` | 82 screenshots of today's UI, plus `capture-log.txt`. |
| `avatar-test-{light,dark}.png` | The pose grid used to pick each mockup's frame. |

The sources are under `design/ui-review/`:

- `mockups/`: `mock.html`, `mock.js`, `mock.css` and `sheet.html`.
- `scripts/`:
  - `capture-current.mjs`: shoots the live UI from a spare Jig.
  - `capture-built.mjs`: shoots the built UI from a spare Jig into `assets\jig-ui-built\`, checking each shot (the page
    never scrolls as a whole, one Jig on screen and never under 112px, WCAG AA for every visible text).
  - `testids.py`: lists the test ids the tests and demos use and any the UI no longer has.
  - `render.mjs`: a static server plus Playwright. It runs as `avatar-test`, `mockups [a|b|c]` or `sheets`.
  - `contrast.mjs`: measures the new text pairings.

To render again: `node design/ui-review/scripts/render.mjs mockups`, then `node design/ui-review/scripts/render.mjs sheets`.
