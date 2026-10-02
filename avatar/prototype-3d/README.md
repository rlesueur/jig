# Jig — three.js 3D avatar prototype

Throwaway-quality prototype to decide whether a real-time 3D Jig is the right path. Nothing here is wired into the
product; `avatar/jig-avatar.js` (the 2D canvas element) is untouched.

## Run

```powershell
cd avatar/prototype-3d
python -m http.server 8763 --bind 127.0.0.1
# open http://127.0.0.1:8763/
```

Everything is local: three.js is vendored, so there are no CDN requests. Query parameters:
`?state=thinking`, `?state=working&task=coding`, `?theme=light`, `?size=small` (240 px stage),
`?controls=0` (hide the strip), `?reduced=1|0` (force reduced motion on or off).

## Files

- `index.html`: demo page with the control strip (states, theme toggle, reduced-motion toggle, FPS readout).
- `jig3d.js`: the `<jig-avatar-3d>` custom element (scene, shaders, behaviour).
- `vendor/three.bundle.min.js`: three.js r186 plus `EffectComposer`, `RenderPass`, `UnrealBloomPass` and `OutputPass`, minified into one ES module.
- `vendor/three-entry.js`, `vendor/build-vendor.mjs`: how the bundle was made (`node vendor/build-vendor.mjs <node_modules with three + esbuild>`).

## Element API

The API mirrors `<jig-avatar>`: the attributes are `state`, `task`, `background`, `audio-level`, `framing` and `reduced-motion`, and
`theme` is `dark` or `light`. The methods are `setState(name, { task, background })` and `setAudioLevel()`, and it fires the `jig-statechange` event.
States are the same as the 2D avatar's `STATES` plus **`listening`**, which does not exist in the 2D element yet. The aliases
`resting`, `sleeping`, `needs-approval`, `asking` and `blocked` work too. The tasks are `browsing`, `writing`, `coding`, `shopping` and `scheduling`.
`shopping` currently reuses the browsing panel and `scheduling` reuses the writing panel.

## What is procedural

Everything. No meshes, textures, rigs or images are loaded.

- **Head**: an ellipsoid with a fresnel rim shader (dark core, violet-to-cyan rim) and faint twinkling speckles.
- **Horn-ears**: cupped crescent blades built along cubic Bézier spines, with an orange-gold inner gradient and violet edges.
  They follow head motion on springs, perk up when attentive and droop when sad.
- **Eyes**: real 3D eyeballs. The iris is amber with radial striations, the pupil dilates, and the highlights are fixed in view space so they stay put as the eye turns.
  Separate eyelid shells handle blinks, squints, happy "^ ^" eyes and closed eyes.
- **Mouth**: a signed-distance smile on a patch of the head sphere. Its curvature and openness are driven by the pose and by `audio-level`.
- **Arms**: tapered glowing tubes rebuilt every frame from two-bone IK (with an elbow pole), plus five fingers per hand with per-finger curl.
- **Body**: 36 ribbons and strands computed entirely in a vertex shader. They spiral from the torso down to a single point, with
  phase-accumulated swirl, waves, sway and spring-lagged drag. Back strands are dimmed for depth.
- **World**: a rippling pool shader, 12 glass shards (`MeshPhysicalMaterial` with transmission, iridescence and a procedural
  PMREM environment) with glowing edges, and 300 GPU sparkles.
- **Props**: research panel (scrolling page, reading line), writing pad (handwriting revealed under the pen), coding tablet
  (code lines typed in irregular bursts), thinking motes, sound-wave arcs for listening, and a "?" for approval.
- **Post-processing**: HDR half-float target with 4× MSAA, `UnrealBloomPass` with its mip weights pushed towards the fine levels
  (tight halos, no fog over the dark head), then sRGB output.

### Lifelike behaviour

- **Breathing**: rate and depth wander on layered noise, and the inhale and exhale are asymmetric.
- **Blinks**: exponential intervals, roughly 18% double blinks, an asymmetric close and open, longer gaps when focused, and blinks triggered by large saccades.
- **Gaze**: the eyes jump in fast saccades on a stiff spring and make micro-saccades between them. The head follows part of the way on a soft spring, and each eye
  aims from its own socket so near targets converge. Gaze priority is the task (reading fixations, the pen tip, the code cursor), then the pointer, then eye contact,
  then idle wandering.
- **Follow-through**: springs drive the hands (slightly under-damped, so they settle on stop), the ears and the ribbon drag.
- **Transitions**: every state change gives a squash-and-stretch kick and a small hop.
- **Fidgets**: random idle events every few seconds (ear flicks, head tilts, glances away, finger wiggles, shrugs, little hops), so the motion never visibly loops.
- **Light theme**: a "re-ink" rather than a filter. The glow shaders use premultiplied blending whose alpha is scaled by the theme mix,
  so the dark theme is additive light and the light theme is "over" ink on paper, and cross-fading between them is continuous. The ink colours come from the
  same `inkOf()` mapping as the 2D avatar. In the light theme bloom drops to almost nothing and its threshold sits above paper white.
- **`prefers-reduced-motion`** (or the `reduced-motion` attribute): swirl, sway, waves, orbits and pulses are scaled down by roughly 4–5×, and
  hops and squash are reduced. Measured: swirl 0.85 → 0.19 rad/s, sway 0.46 → 0.22 rad/s. Blinks and gaze still work.

## Measured (RTX 5090, Chromium 1243 headless via Playwright, ANGLE/D3D11)

| | 1280×720 | 240×240 |
|---|---|---|
| GPU-synced frame cost (`benchmark()`: render plus `readPixels` sync) | ~0.97–1.06 ms (~950–1,000 fps) | ~0.88–0.94 ms |
| CPU time per frame (JS and draw submission) | ~0.38 ms | ~0.38 ms |
| rAF frame rate with vsync on / off | 60 / ~2,200–2,400 | 60 / ~2,300–2,450 |

- **Transferred**: 761 KB three.js bundle, 80 KB `jig3d.js` and 4 KB HTML, about 845 KB in total. `http.server` does no compression; with gzip it would be
  194 KB + 24 KB + 2 KB, about 220 KB (brotli about 180 KB).
- **First render** (navigation to the first composed frame, cold browser): **461 ms** on localhost, most of it shader compilation.
  A second page in the same browser (warm program cache) took 70 ms.
- **Console**: no errors. There is one Direct3D compiler warning (`X4122 … 0.996094`) from three.js's own depth-packing constants
  inside the `MeshPhysicalMaterial` program. It is harmless and appears only under ANGLE/D3D.

## Honest limitations

- The ribbons read as a spiral "tornado" more than the reference's broad S-curved sweep. The silhouette needs authored
  curves (a spline library per state) rather than one parametric funnel.
- The arms are tubes with simple fingers. Hands do not have real palms or poses, and fingers fan in screen space.
- There is no self-occlusion awareness. Targets were hand-tuned so the arms do not pass through the head, and there is no collision.
- Glass shards show mostly their glowing edges. Transmission has little to refract against a dark background.
- One WebGL context per element. Several avatars on one page (for example a chat list) would need a shared renderer or offscreen
  rendering into canvases.
- No WebGL fallback: the element throws if WebGL2 is unavailable. The 2D `<jig-avatar>` remains the answer there.
- Tuning is by eye from a handful of screenshots. Values are not authored poses.
