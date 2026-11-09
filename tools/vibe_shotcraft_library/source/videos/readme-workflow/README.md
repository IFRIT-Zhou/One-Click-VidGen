# README workflow animation

Editable source for the shared workflow banner in the English, Chinese and
Japanese root READMEs. The approved design is a silent, eight-second loop at
1440 × 540; the GIF is exported at 15 fps and loops forever.

From this directory:

```sh
npm run dev
npm run check -- --snapshots
npm run render -- --format gif --gif-loop 0 --fps 15 --quality delivery --output ../../assets/readme/workflow.gif
```

Review the Studio preview before exporting changes. The project scripts pin
HyperFrames 0.8.149; Node.js, Chromium and FFmpeg are required for local rendering.

`index.html` contains the seven-stage rail and its deterministic GSAP timeline.
`index.motion.json` checks readability, framing and motion. The installed
`onboarding-stepper-flow` registry component is retained as the design reference;
the final banner lives in `index.html`.

The full-frame `#pipeline` paints its own charcoal background so transparent
export formats cannot drop the page background.

Keep preview caches, snapshots and intermediate renders out of Git. The shipped
asset is [`workflow.gif`](../../assets/readme/workflow.gif).
