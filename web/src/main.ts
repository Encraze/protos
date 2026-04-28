import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import { loadCurrentUser } from "./store/auth";
import "./style.css";

loadCurrentUser().finally(() => {
  createApp(App).use(router).mount("#app");
});
