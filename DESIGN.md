# VoltWatch interface system

VoltWatch is a fleet operations tool. The main task is to locate a bus, recognize its most urgent battery condition, then inspect the measurements behind it. The driver has a separate, deliberately quieter display. The visual references do not change the telemetry rules or process architecture.

## Direction and alternatives

The selected direction is a compact operations workspace: a stable navigation rail, clear page toolbar, searchable vehicle list, geographic map and right-hand inspector. It follows the task structure in the supplied fleet-product screenshots. A card-based analytics overview was considered but gives charts priority over locating a bus. A cinematic dark command centre was also considered but decorative effects would compete with safety states. The operations direction serves both inspection and presentation with the existing plain HTML/CSS/JavaScript stack.

The utility uses a vehicle selector and one active workspace, rather than five copies of a long form. Its sections follow the work: source file, playback, injected event, driver access. All buses remain independently controllable. The driver keeps status, one short action, charge estimate and data availability together; diagnostics remain parked/demo-only.

## Foundations

Use the locally vendored Manrope variable font with system-ui / Segoe UI fallback. Root size is 16px; UI text is 14px, supporting text 12–13px, section headings 16–18px and page titles 24px. Express sizes in rem, weight 400–700, unitless line height 1.5 for body and 1.2 for headings. Headings use restrained negative tracking; small uppercase labels use 0.05em. Changing numbers use tabular figures. Mobile inputs are at least 16px. Headings balance; paragraphs wrap naturally and long identifiers break rather than escape.

Use a 4px spacing scale: 4, 8, 12, 16, 24, 32. Gaps between groups exceed gaps within a group. Align labels, inputs and section content to shared edges. Buttons/inputs use a 6px radius; larger standalone panels 10px. Nested visible insets follow concentric radii. Dividers establish data structure; restrained shadow establishes overlay elevation.

| Role | Light | Dark |
| --- | --- | --- |
| Page | `#f5f7f8` | `#111112` |
| Surface | `#ffffff` | `#19191b` |
| Raised/hover surface | `#edf1f3` | `#222225` |
| Primary text | `#202b35` | `#ededf0` |
| Secondary text | `#56636e` | `#b1b1b8` |
| Structure | `#dce2e7` | `#363638` |
| Input boundary | `#778591` | `#85858c` |
| Action/selected text | `#285d91` | `#bec6d4` |
| Action fill / foreground | `#285d91` / white | `#bec6d4` / `#111112` |
| Normal text | `#426353` | `#b6bcb6` |
| Warning text | `#87500b` | `#eec17c` |
| Critical text | `#a42f42` | `#ff9bac` |
| Focus | `#285d91` | `#d1d8e2` |

Map these to semantic CSS custom properties. Use one action hue; status hues communicate measurement states only, always with words. Navigation uses the theme’s neutral surfaces. The user approved an additional liquid-glass treatment using the exact @samasante/liquid-glass 0.1.1 library. Apply it only as an optical backing to selected panels, leaving text and controls crisp and warning/critical surfaces opaque. Use subdued frost/refraction without neon glow or metallic bevels. Provide persistent reduced-transparency controls and an opaque forced-colors fallback. Preserve the earlier opaque previews unchanged. Icons, if used, come from one existing open-source library and accompany accessible labels. Do not fabricate navigation destinations.

## Components and interaction

Vehicle rows show bus identity, status and useful charge/temperature context. Status labels are compact, intrinsic-width and non-stretching. Search has a persistent label and a recoverable empty state. The map retains attribution, simulated-position labelling and explicit tile-failure feedback. Its markers are also selectable through the vehicle list. Streets uses OpenFreeMap Positron/Dark vector tiles; dark roads and labels have deliberately stronger contrast. Satellite uses actual EOxCloudless 2024 imagery with visible provider/year/license credit. Theme and basemap switches preserve the camera, selected vehicle and route. Route paths remain owned by Sahibjot’s backend; vehicle IDs and route labels stay distinct.

The inspector orders information: bus/status, current summary, active alerts, twelve-module selection, selected-module trends, then raw source/provenance details behind a named disclosure. Quality issues and incomplete-history notices stay visible. Charts share the same time axis and semantic threshold lines and have a table alternative. Selection, focus and scroll position survive polling. Show waiting, no-signal and history-sync states honestly.

All controls have explicit hover, focus, pressed, disabled, busy and error behavior. Use native buttons, labels, selects and disclosures. Theme switching is immediate; frequent selection and telemetry updates do not animate. Any drawer movement uses only transform/opacity with a reduced-motion fallback. Do not animate safety states or add routine sounds. Offer a visibly labelled freeze/resume control on the operator view; frozen data must say it is frozen. Keep critical driver information live.

Desktop uses the full workspace; tablet reduces navigation/list width before hiding content. At narrow widths navigation becomes a compact top row, the fleet becomes a normally scrollable section, and the inspector flows below the map or takes a clearly dismissible full-width panel. At 320px and 200% zoom, controls and text reflow without page-wide horizontal scrolling. Maps/tables may remain internally two-dimensional. Drawers contain overscroll. Preserve focus visibility, skip links and forced-color rendering.

## Reference basis

The [awesome-design-md collection](https://github.com/VoltAgent/awesome-design-md) contains inspired interpretations, not official product specifications. Its Stripe example informed numeric hierarchy and quiet surfaces; Vercel informed compact controls and restrained neutral structure; Sentry informed separation of severity from ordinary chrome. Their marketing gradients, proprietary fonts and mascots do not suit this application.

The public [Refero dashboard gallery](https://refero.design/search?page_types%5Bid%5D%5B%5D=28&order=trending) exposes six previews, including Mercury, Shopify and Glassnode: persistent navigation, aligned numeric summaries and chart context are useful patterns. The full gallery is behind sign-in; no restricted flows or Refero MCP were accessed. The supplied fleet screenshots are the closest task reference.

[Interfaces' free craft article](https://interfaces.dev/magazine/issues/details-that-make-interfaces-feel-better) informs wrapping, tabular figures, optical alignment and restrained elevation.

## Approved implementation

The November 21 rollout follows Sahibjot’s compact icon rail, floating vehicle inspector and full ETS route metadata. His backend choices, including per-bus sustained demo scenarios, are authoritative. The separate driver view follows the reviewed status/charge/parked-diagnostics hierarchy. Changes are implemented on the design branch and verified before the user-authorized local main merge; no push or deployment is implied. The glass library is bundled locally with React as a small backing-layer island; the existing HTML/JavaScript application and process architecture remain in place. Browser fidelity of the backdrop-refraction path was checked in Chrome; Safari/Firefox refraction is not claimed verified.
