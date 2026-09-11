# Design

## Direction contract

**Thesis:** The Space itself is the interface. Confirmed knowledge grows as a living brain instead of being hidden behind a conventional grid of dashboard cards.

**World:** Sculpted game-console forms, violet volume, cyan neural energy, white operational surfaces, and chunky original agent avatars. The style borrows the approachable energy of social games without copying Roblox assets or branding.

**Story:** A user immediately sees that memory is active, who is connected, who feeds or consumes it, and which events were durably confirmed.

## Color roles

- Ink / navigation: `#17143f`
- Neural violet: `#6d4aff`
- Read-flow cyan: `#50dcff`
- Healthy/online mint: `#67f0b2`
- Checkpoint gold: `#ffcb57`
- Operational surface: `#ffffff`
- Page ground: `#f5f2ff`
- Secondary text: `#665f86`

Color never acts as the only state indicator.

## Shape and depth

- Operational panels use 16px corners and soft, downward neutral shadows.
- The brain world uses a larger 22px boundary and deeper neutral elevation.
- Agent avatars are original compact blocks with inset depth, not third-party logos.
- Pills are reserved for small statuses; content does not become a field of cards.

## Motion

- Personality: playful for the brain, direct for operational controls.
- Ambient neural breathing is subtle and pauses under `prefers-reduced-motion`.
- A node is created visually only after durable acknowledgement.
- Agent-to-brain means write; brain-to-agent means read; gold means checkpoint; red means conflict.
- Reconnection uses the journal cursor and must not replay duplicate events as new activity.

## Accessibility and fallback

- Canvas has a textual live summary and event list.
- Keyboard focus is visibly gold.
- Reduced-motion users receive a static neural view with full data access.
- Mobile rearranges the brain first, followed by agents and activity.
- Later WebGL work must preserve Canvas/SVG/static fallbacks.

## Current implementation boundary

The first slice uses Canvas 2D and initial event loading. Real push streaming, authenticated panel login, full agent presence, and 3D/WebGL progression remain future work.
