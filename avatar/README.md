# Jig avatar

An animated, dependency-free web component for Jig, the always-on personal agent that runs only on local models. The mascot is drawn live as a vector rig on a single `<canvas>`. Its head, leaf ears, blinking eyes, mouth, arms, ribbon tail, prism shards, sparks and vortex are separate parts, so every state can move them independently. Nothing is a pre-rendered raster.

`assets/jig-mascot-reference.jpg` is the approved artwork the rig is matched against.

## Run the demo

```powershell
cd jig\avatar
python -m http.server 8765 --bind 127.0.0.1
```

Open <http://127.0.0.1:8765/>. You can deep-link to a state with query parameters, for example `?state=working&task=coding`, `?state=working&task=browsing&background=1` or `?state=talking&audio=0.6`. Add `theme=light` to see the light theme, or use the Theme buttons.

## Usage

```html
<script type="module" src="./jig-avatar.js"></script>

<jig-avatar state="idle" style="width: 320px; height: 320px"></jig-avatar>
<jig-avatar state="working" task="browsing" shape="circle" style="width: 48px; height: 48px"></jig-avatar>
```

```js
import { STATES, TASKS } from './jig-avatar.js';

const jig = document.querySelector('jig-avatar');
jig.setState('thinking');
jig.setState('working', { task: 'writing' });
jig.setState('working', { task: 'browsing', background: true }); // dimmed: read-only background research
jig.setState('talking');
jig.setAudioLevel(0.42); // call every frame or on each audio chunk while on a call
jig.addEventListener('jig-statechange', (e) => console.log(e.detail)); // { state, task, previous, background }
jig.setState('dance'); // the full dance, looping
await jig.dance(); // or a short flourish over the current state; resolves true when done
```

### API

| Member | Description |
| --- | --- |
| `setState(name, { task, background })` | Moves smoothly to a new state. `task` is required for `working` and not allowed for any other state. `background` is optional (default `false`) and works with any state. |
| `setAudioLevel(level)` | Sets the live voice level, from 0 to 1, used by `talking`. In the `dance` state it makes the steps livelier, and sharp rises add an extra hop, so Jig can dance to music. |
| `dance({ routine })` | Plays a one-off dance over the current state and returns a promise. `routine` is `flourish` (default, 8 beats: jig steps, a spin and a ta-da), `celebrate` (8 beats, the success dance) or `dance` (the full 16-beat routine, once). The promise resolves `true` when the dance finishes, `false` if it is cut short (a newer dance replaces it, or Jig switches to `dance` or `paused`), and `false` straight away when reduced motion is on, because Jig doesn't dance then. Throws while `paused`, or for an unknown routine. |
| `step(dt)`, `advance(seconds, fps = 60)` | Manual clock only (`clock="manual"`). `step` advances by `dt` seconds (more than 0, up to 1) and draws one frame. `advance` steps through `seconds` in fixed `1/fps` steps. |
| `time` | Seconds of animation run so far. |
| `tempo` | Dance tempo in beats per minute, from the `tempo` attribute (default 120, exported as `DEFAULT_TEMPO`). |
| `dancing` | `true` while the dance state or a flourish is moving Jig. |
| `state`, `task`, `background`, `audioLevel` | Read-only getters. |
| `jig-statechange` event | Fires on every change, with `detail: { state, task, previous, background }`. |
| `STATES`, `TASKS` | Exported arrays of the valid names. |

Invalid input throws instead of guessing. An unknown state, an unknown task, a missing task for `working`, a `background` value that is not a boolean, or an audio level outside 0–1 raises a `RangeError` or `TypeError`. Invalid attribute values (`shape`, `framing`, `theme`, `seed`, `clock`, `tempo`, `look-at`) throw from the attribute callback, so the browser reports them as errors.

### Background work

`background: true` marks the state as background work, such as read-only proactive research. The animation stays the same (for example the browsing panes and scan sweep), but the rig dims, the eyes go half-lidded, and the motion softens and slows. The change is interpolated like any other state change, so switching the flag on or off cross-fades. Each `setState` call describes the whole state, so leaving `background` out sets it back to `false`.

The `background` option is separate from the `background` state alias, which still means `monitoring`.

### Attributes

| Attribute | Values |
| --- | --- |
| `state`, `task` | Mirror `setState` and stay in sync with it. |
| `background` | Boolean attribute that mirrors the `background` option. Present (or any value other than `false`) means background work. |
| `audio-level` | 0–1. |
| `shape` | `rounded` (default), `circle` for avatars and icons, or `none` for a transparent background. |
| `framing` | `auto` (default; switches to `icon` below 110px), `icon` (head-and-shoulders crop with reduced detail), or `full`. |
| `reduced-motion` | Forces reduced motion. Otherwise the component follows `prefers-reduced-motion`. |
| `theme` | `dark` (default) or `light`. Also available as the `theme` property. Any other value throws a `RangeError`. |
| `look-at` | Where Jig looks when nothing else needs its eyes: `"x,y"` as fractions of the avatar's own box (`"0.5,1.6"` is below it), or a CSS selector such as `"#composer"` to look at the centre of that element. A selector that matches nothing is reported once with `reportError`, and Jig carries on with its own glances. The pointer takes priority while it is over the avatar. |
| `user-typing` | Boolean. While present, idle never starts a dance flourish, and a flourish already playing stops at the next beat. Set it from your message box's `input` events and clear it a second or so after the last keystroke. |
| `tempo` | Dance tempo, 40–220 beats per minute (default 120). |
| `seed` | A whole number. Fixes every random choice (blinks, glances, fidgets, flourish timing, confetti), so the same seed gives the same performance. Without it each avatar picks its own seed. |
| `clock` | `auto` (default: the shared ticker) or `manual` (nothing moves until you call `step()`). |

Size the element with CSS.

### Life and motion

Jig is never still and never visibly loops. These run in every state, scaled to the state's energy:

- **Breathing** with a rate and depth that wander (layered value noise), swelling the head and lifting the shoulders. It keeps going, slowly, even when paused.
- **Blinks** at irregular intervals (an exponential distribution), sometimes a double blink, often a blink on a state change or a big glance. The upper lids have curved edges, so half-closed eyes look content, not sly.
- **Gaze.** Eyes dart in small saccades and settle on springs. The head turns and tilts a little towards where Jig looks. When the pointer is over the avatar, Jig watches it; otherwise it follows `look-at`, the task (panes, page, terminal), or glances around by itself and often back at you.
- **Follow-through.** Ears, arms, fingers and ribbons are on damped springs. Ears flop with every bob and hop, fingers drag behind a swinging arm, and the ribbons trail the head, so movements overshoot slightly and settle rather than stop dead.
- **Squash and stretch** with a small hop on every state change.
- **Eyes** are clean round eyes with catch-lights. Expressions come from the upper lids, the eye shape (happy eyes and winks are upturned arcs) and the brows; there are no lower lids.
- **Arms stay in view.** Arms are drawn behind the head, so each frame any arm, hand or finger that would pass behind it swings outwards until it clears the head by a margin (wider at icon sizes). This holds through the dance, the spin, fidgets and celebrations.
- **Fidgets** every few seconds, chosen at random: an ear flick, a head tilt, a fresh glance, a finger wiggle, a stretch or (in idle) a little hop.
- **Groove.** Working states bob to a subtle rhythm, and talking a gentler one.

### Dance

Jig's name is a dance, so it has a repertoire:

- **`dance` state** (aliases `dancing`, `jig`) loops a 16-beat routine, 8 seconds at the default 120 bpm: eight beats of side-steps on the ribbon foot with the body leaning against the step and arms swinging in time; four beats of both hands up, waving, with quick double taps; a two-beat spin with a happy face and the ribbons swirling round; and a ta-da with arms up in a V and a wink (one eye becomes a happy upturned arc). Every landing squashes the body, flops the ears and sends a ripple across the pool. The routine starts from beat 0 each time the state is entered, which keeps video takes repeatable.
- **`success`** plays the 8-beat `celebrate` routine once (waving hops, a spin, a ta-da) with the confetti burst, then settles into a happy bob.
- **Idle flourishes.** Idle breaks into the 8-beat flourish by itself, first after 60–120 seconds and then every 60–135 seconds, never in background work or while `user-typing` is set.
- **`dance()`** plays a flourish on demand.

With reduced motion there is no dancing at all. The `dance` state shows a happy face with calm breathing and blinks, success skips the celebration, and `dance()` resolves `false`.

### Rendering video frame by frame

For reproducible renders, for example a teaser video, set a seed and drive the clock yourself:

```html
<jig-avatar id="jig" clock="manual" seed="7" tempo="120" framing="full" style="width: 1080px; height: 1080px"></jig-avatar>
```

```js
const jig = document.getElementById('jig');
jig.setState('dance');
for (let frame = 0; frame < 240; frame++) {
  jig.step(1 / 60); // two 60 Hz steps per 30 fps frame keeps the springs identical to live playback
  jig.step(1 / 60);
  await captureFrame(); // for example a Playwright screenshot of the element
}
```

With the same seed, size, state changes and steps, every frame is pixel-identical on every run. The pointer is ignored on the manual clock, so a stray mouse cannot change a take. `look-at` still works, for scripted glances. To dance to a soundtrack, call `setAudioLevel()` with the track's loudness for each frame before stepping.

### Light theme

`theme="light"` draws the same character with the same rig, for cream and other light pages. Neon glow relies on additive light, which disappears on a pale background, so the light pass re-inks the drawing instead:

- **Colour.** Every ribbon, shard, spark and prop colour keeps its hue, but drops to a deep, more saturated lightness, like printed ink. Near-white highlights become plum ink. Strokes are drawn normally rather than added together, so nothing turns white where colours overlap.
- **Depth.** The outer glow becomes a soft coloured drop-shadow below each stroke. Each line has a solid ink body and a thin satin highlight. Radial glows drop to under half strength, and a soft plum ground shadow sits under the tail.
- **Head.** The head stays a dark plum, slightly warmer and lighter at the top left, with a soft shadow. It is Jig's silhouette in both themes, and it keeps the amber eyes as bright as in dark mode.
- **Background.** The background is always transparent, whatever the `shape`. In light mode Jig never sits in a dark porthole. `shape="circle"` still clips the drawing to a circle.
- **Dimming.** Background work and the paused state fade towards the page instead of towards black. State signs (thinking bubbles, talk arcs, pause bars, the approval bubble and the error cross) always draw at full strength, so they stay legible.

Changing `theme` cross-fades between the two renderings over 0.6 s (0.15 s with reduced motion). The first frame uses the theme set at connection, with no fade. Without the attribute, the component renders exactly as before.

```js
jig.theme = 'light'; // or jig.setAttribute('theme', 'light')
const mq = matchMedia('(prefers-color-scheme: dark)');
jig.theme = mq.matches ? 'dark' : 'light';
``` The canvas follows it through a `ResizeObserver` and renders at the device pixel ratio, capped at 2.

## States

Every pose parameter is interpolated, so changing state never produces a hard cut. The prisms morph between layouts by blending their vertices, and task overlays fade in as their weight rises.

| State | Animation |
| --- | --- |
| `idle` (alias `resting`) | Gentle bob and irregular breathing, slow ribbon sway, blinks, glances and fidgets, a soft smile, tumbling glass shards drifting slowly. Now and then a short dance flourish. |
| `monitoring` (aliases `sleeping`, `background`) | Dimmed with eyes closed, a slow cyan pulse, a radar sweep and sonar rings. An eye occasionally half-opens and glances around. This is read-only proactive research. |
| `thinking` | Swirl and orbit speed up, eyes look upwards, a hand goes up beside the head, sparks form a halo and thought bubbles rise. |
| `working` + `browsing` | Shards become browser panes facing Jig, with a scan sweep. Eyes jump between panes and a reading beam follows them. |
| `working` + `writing` | A page and an envelope appear. An amber ribbon from the hand writes looping cursive lines, then the envelope flashes as "sent". |
| `working` + `coding` | A terminal pane types coloured code with a blinking caret. Sparks turn into rising code glyphs, and hands tap in a typing rhythm. |
| `working` + `shopping` | Jig holds a payment card with a shimmer and a confirmation tick, while £ coins spin on a ring around the tail. |
| `working` + `scheduling` | Shards become a calendar page of day tiles, with a ticking highlight and a booked tick. A clock ring with a sweeping hand circles the head. |
| `talking` (aliases `call`, `on-call`) | The mouth opens with the audio level, ribbons widen and ripple, sound arcs radiate and the vortex brightens. |
| `approval` (alias `needs-approval`) | A cute wave with a curling hand, a bigger bounce, an amber tint, amber pulse rings and an alert bubble. |
| `success` | A celebration dance (waving hops, a spin and a ta-da), a confetti burst, then happy ^ ^ eyes, an open smile and both arms up. |
| `error` (alias `blocked`) | Worried and apologetic, never cross: the head sinks and tilts, ears droop, arms hang, the tail narrows, brows lift in the middle over big warm eyes, a small soft frown. Colours cool towards blue (the eyes stay amber), shards sink and a small × and sweat drop appear. |
| `paused` | Peacefully asleep: eyes closed in contented curves, a small smile, drooping ears, slow breathing. Motion slows almost to a stop, colours desaturate and a pause badge appears. |
| `dance` (aliases `dancing`, `jig`) | The full dance routine, looping (see Dance above). |

The state set follows how Meta Muse and OpenAI Dots present always-on agents. Both keep working in the background, return when they need a decision or approval (for example before sending or buying), run proactive read-only research, support calls, and can be paused. Jig adds per-task working variants so the user can tell at a glance what kind of work is under way.

## Mapping agent runtime events to states

Drive the avatar from your runtime's event stream. Keep a single source of truth, and call `setState` only when the derived state changes.

| Runtime event | Avatar call |
| --- | --- |
| Agent ready with nothing queued | `setState('idle')` |
| Scheduled watchers active, with no research running | `setState('monitoring')` |
| Model generating a plan or reasoning, before any tool call | `setState('thinking')` |
| Tool call: web search, fetch, browser navigation or page reading | `setState('working', { task: 'browsing' })` |
| Proactive read-only research running (reading, fetching or reasoning between tool calls) | `setState('working', { task: 'browsing', background: true })` |
| Tool call: drafting or sending email, messages or documents | `setState('working', { task: 'writing' })` |
| Tool call: shell, code editing, running tests | `setState('working', { task: 'coding' })` |
| Tool call: basket, checkout or payment | `setState('working', { task: 'shopping' })` |
| Tool call: calendar read or write, booking, reminders | `setState('working', { task: 'scheduling' })` |
| Voice session open | `setState('talking')`, then `setAudioLevel(rms)` from the active speaker's audio (Jig's output while it speaks, the user's input while it listens) |
| Action review requires the user (approve, hand off, sign in, connect an app) | `setState('approval')` |
| Task completed successfully | `setState('success')`, then return to `idle` or `monitoring` after about 2–3 seconds |
| Task failed, permission denied, or blocked by a rule | `setState('error')` until the user acknowledges it or the task retries |
| User paused the agent or a task | `setState('paused')` |

Precedence when several things are true at once, highest first: `approval`, `error`, `talking`, `working`, `thinking`, `paused`, background `working`, `monitoring`, `idle`. A pending approval should always win, because it is the one state where Jig needs the user. When parallel tool calls span several task kinds, show the one the user is most likely to care about, or the most recently started.

## Performance and accessibility

- All instances share one `requestAnimationFrame` loop (manual-clock instances leave it). Off-screen instances are skipped through `IntersectionObserver`. No layout is read inside the loop, except two `getBoundingClientRect` calls per frame when `look-at` names a selector.
- The neon glow is built from layered strokes with additive blending rather than `shadowBlur`. The demo page, with 17 avatars dancing, holds 60 fps.
- Icon framing reduces detail: fewer ribbons, sparks and vortex rings, lighter glow passes, no ripple rings.
- With reduced motion, amplitudes shrink to about 15% and phases slow down. There is no dancing, hopping, squash and stretch, fidgeting or confetti, only calm breathing, blinks and gentle eye movement. States still cross-fade quickly.
- The element sets `role="img"` and an `aria-label` such as "Jig is working: coding", "Jig is dancing", or "Jig is working: browsing, in the background" for background work.

## Tests

`tests.html` runs real-browser tests of the component: the dance state and `dance()`, reduced motion, the success celebration, the manual clock and pixel-identical seeded renders, idle flourishes and `user-typing`, blink timing, no blush or lower lids, the wink, arms never hidden behind the head (every 30 fps frame of the dance, the celebration, and both flourishes in both themes at full and icon size, plus 20 s of idle fidgets at full size, comparing arms-only and head-only masks from `layerMask()`), `look-at`, attribute validation, and every state in both themes at large and icon sizes. Serve the folder (see Run the demo) and open <http://127.0.0.1:8765/tests.html>. The summary line reads "N passed, 0 failed", and `window.__results` holds the details for automation.

## Files

- `jig-avatar.js`: the web component and rig.
- `index.html`: the demo page, with state and task buttons, dance controls (flourish and tempo), a gaze demo with a message box, a background-work toggle, an audio slider, a real microphone input and 48px icons.
- `tests.html`: browser tests for the component.
- `assets/jig-mascot-reference.jpg`: the approved mascot artwork.
- `screenshots/`: captured states.
