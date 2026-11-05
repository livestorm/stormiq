import { createApp } from "vue";
import App from "./App.vue";
import { initUiVersion } from "./design/uiVersion";
import router from "./router";
import "./styles.css";
import "./design/tokens.css";

// Decide classic vs v1 design (?ui=v1 / ?ui=classic, remembered per browser)
// before App.vue and the router read it.
initUiVersion();

createApp(App).use(router).mount("#app");
