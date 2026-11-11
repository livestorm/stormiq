<script>
// StormIQ's icon set: Lucide icons (https://lucide.dev, ISC licence, notice in
// LICENSE-lucide.txt next to this file), vendored as SVG data so there is no
// runtime dependency. Livestorm's own icon font is proprietary and can't ship
// in this public repository; each name below is the closest Lucide match for
// the glyph the design uses (docs/brand.md lists the meaning of each one).
//
// Decorative by default (aria-hidden). Pass `label` when the icon carries
// meaning on its own; an icon-only button should put its aria-label on the
// button instead. Colour comes from `currentColor`.
//
// To add an icon, copy its child elements from Lucide's SVG (24×24 grid,
// stroke-based) into ICONS.
const ICONS = {
  "activity": [
    ["path", {d:"M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"}],
  ],
  "arrow-down": [
    ["path", {d:"M12 5v14"}],
    ["path", {d:"m19 12-7 7-7-7"}],
  ],
  "arrow-up": [
    ["path", {d:"m5 12 7-7 7 7"}],
    ["path", {d:"M12 19V5"}],
  ],
  "arrow-up-down": [
    ["path", {d:"m21 16-4 4-4-4"}],
    ["path", {d:"M17 20V4"}],
    ["path", {d:"m3 8 4-4 4 4"}],
    ["path", {d:"M7 4v16"}],
  ],
  "calendar": [
    ["path", {d:"M8 2v4"}],
    ["path", {d:"M16 2v4"}],
    ["rect", {width:"18",height:"18",x:"3",y:"4",rx:"2"}],
    ["path", {d:"M3 10h18"}],
  ],
  "captions": [
    ["rect", {width:"18",height:"14",x:"3",y:"5",rx:"2",ry:"2"}],
    ["path", {d:"M7 15h4M15 15h2M7 11h2M13 11h4"}],
  ],
  "check": [
    ["path", {d:"M20 6 9 17l-5-5"}],
  ],
  "chevron-down": [
    ["path", {d:"m6 9 6 6 6-6"}],
  ],
  "chevron-left": [
    ["path", {d:"m15 18-6-6 6-6"}],
  ],
  "chevron-right": [
    ["path", {d:"m9 18 6-6-6-6"}],
  ],
  "chevron-up": [
    ["path", {d:"m18 15-6-6-6 6"}],
  ],
  "copy": [
    ["rect", {width:"14",height:"14",x:"8",y:"8",rx:"2",ry:"2"}],
    ["path", {d:"M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"}],
  ],
  "download": [
    ["path", {d:"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"}],
    ["polyline", {points:"7 10 12 15 17 10"}],
    ["line", {"x1":"12","x2":"12","y1":"15","y2":"3"}],
  ],
  "external-link": [
    ["path", {d:"M15 3h6v6"}],
    ["path", {d:"M10 14 21 3"}],
    ["path", {d:"M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"}],
  ],
  "file-text": [
    ["path", {d:"M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"}],
    ["path", {d:"M14 2v4a2 2 0 0 0 2 2h4"}],
    ["path", {d:"M10 9H8"}],
    ["path", {d:"M16 13H8"}],
    ["path", {d:"M16 17H8"}],
  ],
  "help-circle": [
    ["circle", {cx:"12",cy:"12",r:"10"}],
    ["path", {d:"M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"}],
    ["path", {d:"M12 17h.01"}],
  ],
  "info": [
    ["circle", {cx:"12",cy:"12",r:"10"}],
    ["path", {d:"M12 16v-4"}],
    ["path", {d:"M12 8h.01"}],
  ],
  "list": [
    ["path", {d:"M3 12h.01"}],
    ["path", {d:"M3 18h.01"}],
    ["path", {d:"M3 6h.01"}],
    ["path", {d:"M8 12h13"}],
    ["path", {d:"M8 18h13"}],
    ["path", {d:"M8 6h13"}],
  ],
  "list-filter": [
    ["path", {d:"M3 6h18"}],
    ["path", {d:"M7 12h10"}],
    ["path", {d:"M10 18h4"}],
  ],
  "lock": [
    ["rect", {width:"18",height:"11",x:"3",y:"11",rx:"2",ry:"2"}],
    ["path", {d:"M7 11V7a5 5 0 0 1 10 0v4"}],
  ],
  "log-in": [
    ["path", {d:"M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"}],
    ["polyline", {points:"10 17 15 12 10 7"}],
    ["line", {"x1":"15","x2":"3","y1":"12","y2":"12"}],
  ],
  "log-out": [
    ["path", {d:"M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"}],
    ["polyline", {points:"16 17 21 12 16 7"}],
    ["line", {"x1":"21","x2":"9","y1":"12","y2":"12"}],
  ],
  "menu": [
    ["line", {"x1":"4","x2":"20","y1":"12","y2":"12"}],
    ["line", {"x1":"4","x2":"20","y1":"6","y2":"6"}],
    ["line", {"x1":"4","x2":"20","y1":"18","y2":"18"}],
  ],
  "message-circle": [
    ["path", {d:"M7.9 20A9 9 0 1 0 4 16.1L2 22Z"}],
  ],
  "moon": [
    ["path", {d:"M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"}],
  ],
  "more-horizontal": [
    ["circle", {cx:"12",cy:"12",r:"1"}],
    ["circle", {cx:"19",cy:"12",r:"1"}],
    ["circle", {cx:"5",cy:"12",r:"1"}],
  ],
  "pause": [
    ["rect", {x:"14",y:"3",width:"5",height:"18",rx:"1"}],
    ["rect", {x:"5",y:"3",width:"5",height:"18",rx:"1"}],
  ],
  "pen-line": [
    ["path", {d:"M12 20h9"}],
    ["path", {d:"M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z"}],
  ],
  "pie-chart": [
    ["path", {d:"M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z"}],
    ["path", {d:"M21.21 15.89A10 10 0 1 1 8 2.83"}],
  ],
  "play": [
    ["polygon", {points:"6 3 20 12 6 21 6 3"}],
  ],
  "plus": [
    ["path", {d:"M5 12h14"}],
    ["path", {d:"M12 5v14"}],
  ],
  "presentation": [
    ["path", {d:"M2 3h20"}],
    ["path", {d:"M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3"}],
    ["path", {d:"m7 21 5-5 5 5"}],
  ],
  "refresh-cw": [
    ["path", {d:"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"}],
    ["path", {d:"M21 3v5h-5"}],
    ["path", {d:"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"}],
    ["path", {d:"M8 16H3v5"}],
  ],
  "search": [
    ["circle", {cx:"11",cy:"11",r:"8"}],
    ["path", {d:"m21 21-4.3-4.3"}],
  ],
  "settings": [
    ["path", {d:"M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"}],
    ["circle", {cx:"12",cy:"12",r:"3"}],
  ],
  "share-2": [
    ["circle", {cx:"18",cy:"5",r:"3"}],
    ["circle", {cx:"6",cy:"12",r:"3"}],
    ["circle", {cx:"18",cy:"19",r:"3"}],
    ["line", {"x1":"8.59","x2":"15.42","y1":"13.51","y2":"17.49"}],
    ["line", {"x1":"15.41","x2":"8.59","y1":"6.51","y2":"10.49"}],
  ],
  "shield": [
    ["path", {d:"M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"}],
  ],
  "star": [
    ["path", {d:"M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"}],
  ],
  "sun": [
    ["circle", {cx:"12",cy:"12",r:"4"}],
    ["path", {d:"M12 2v2"}],
    ["path", {d:"M12 20v2"}],
    ["path", {d:"m4.93 4.93 1.41 1.41"}],
    ["path", {d:"m17.66 17.66 1.41 1.41"}],
    ["path", {d:"M2 12h2"}],
    ["path", {d:"M20 12h2"}],
    ["path", {d:"m6.34 17.66-1.41 1.41"}],
    ["path", {d:"m19.07 4.93-1.41 1.41"}],
  ],
  "table": [
    ["path", {d:"M12 3v18"}],
    ["rect", {width:"18",height:"18",x:"3",y:"3",rx:"2"}],
    ["path", {d:"M3 9h18"}],
    ["path", {d:"M3 15h18"}],
  ],
  "target": [
    ["circle", {cx:"12",cy:"12",r:"10"}],
    ["circle", {cx:"12",cy:"12",r:"6"}],
    ["circle", {cx:"12",cy:"12",r:"2"}],
  ],
  "timer": [
    ["line", {"x1":"10","x2":"14","y1":"2","y2":"2"}],
    ["line", {"x1":"12","x2":"15","y1":"14","y2":"11"}],
    ["circle", {cx:"12",cy:"14",r:"8"}],
  ],
  "user-check": [
    ["path", {d:"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"}],
    ["circle", {cx:"9",cy:"7",r:"4"}],
    ["polyline", {points:"16 11 18 13 22 9"}],
  ],
  "users": [
    ["path", {d:"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"}],
    ["circle", {cx:"9",cy:"7",r:"4"}],
    ["path", {d:"M22 21v-2a4 4 0 0 0-3-3.87"}],
    ["path", {d:"M16 3.13a4 4 0 0 1 0 7.75"}],
  ],
  "volume-2": [
    ["path", {d:"M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"}],
    ["path", {d:"M16 9a5 5 0 0 1 0 6"}],
    ["path", {d:"M19.364 18.364a9 9 0 0 0 0-12.728"}],
  ],
  "volume-x": [
    ["path", {d:"M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"}],
    ["line", {x1:"22",x2:"16",y1:"9",y2:"15"}],
    ["line", {x1:"16",x2:"22",y1:"9",y2:"15"}],
  ],
  "x": [
    ["path", {d:"M18 6 6 18"}],
    ["path", {d:"m6 6 12 12"}],
  ],
  "zap": [
    ["path", {d:"M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"}],
  ],
};

/** Every icon name, for the design gallery. */
export const ICON_NAMES = Object.keys(ICONS);
</script>

<script setup>
import { computed } from "vue";

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
  strokeWidth: { type: [Number, String], default: 2 },
  label: { type: String, default: "" },
});

const nodes = computed(() => {
  const found = ICONS[props.name];
  if (!found && import.meta.env.DEV) console.warn(`Unknown icon "${props.name}"`);
  return found || [];
});
</script>

<template>
  <svg
    class="siq-icon"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    :width="size"
    :height="size"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    focusable="false"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
  >
    <component :is="node[0]" v-for="(node, index) in nodes" :key="index" v-bind="node[1]" />
  </svg>
</template>

<style scoped>
.siq-icon {
  display: inline-block;
  flex: none;
  vertical-align: middle;
}
</style>
