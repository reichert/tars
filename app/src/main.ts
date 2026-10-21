import { createApp } from "vue";
import App from "./App.vue";
import "./styles/base.css";
import { initAppearance } from "./composables/useAppearance";

initAppearance();

createApp(App).mount("#app");
