import { createRouter, createWebHistory, type RouteLocationNormalized } from "vue-router";
import HomePage from "@/pages/HomePage.vue";
import LoginPage from "@/pages/LoginPage.vue";
import ProviderKeysPage from "@/pages/ProviderKeysPage.vue";
import ApiKeysPage from "@/pages/ApiKeysPage.vue";
import { authLoaded, currentUser, loadCurrentUser } from "@/store/auth";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: LoginPage, meta: { public: true } },
    { path: "/", name: "home", component: HomePage },
    { path: "/keys/providers", name: "provider-keys", component: ProviderKeysPage },
    { path: "/keys/platform", name: "api-keys", component: ApiKeysPage },
  ],
});

router.beforeEach(async (to: RouteLocationNormalized) => {
  if (!authLoaded.value) {
    await loadCurrentUser();
  }
  if (to.meta.public) {
    if (currentUser.value && to.name === "login") {
      return { name: "home" };
    }
    return true;
  }
  if (!currentUser.value) {
    return { name: "login" };
  }
  return true;
});
