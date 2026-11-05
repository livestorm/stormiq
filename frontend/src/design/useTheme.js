import { readonly, ref } from "vue";
import { createThemeController } from "./theme";
import { safeStorage } from "./uiVersion";

// One theme controller per page, shared by every component that asks for it.
let controller = null;
const theme = ref("light");
const choice = ref(null);

function ensureController() {
  if (controller || typeof window === "undefined") return;
  controller = createThemeController({
    storage: safeStorage(window),
    matchMedia: typeof window.matchMedia === "function" ? window.matchMedia.bind(window) : null,
    root: document.documentElement,
  });
  theme.value = controller.theme;
  choice.value = controller.choice;
  controller.subscribe((current) => {
    theme.value = current.theme;
    choice.value = current.choice;
  });
}

/** The theme on screen ("light" or "dark"), the explicit choice (or null), and setters. */
export function useTheme() {
  ensureController();
  return {
    theme: readonly(theme),
    choice: readonly(choice),
    toggle: () => controller?.toggle(),
    setChoice: (next) => controller?.setChoice(next),
  };
}
