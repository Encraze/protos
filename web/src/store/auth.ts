import { ref } from "vue";
import { fetchMe, type CurrentUser } from "@/api/auth";

export const currentUser = ref<CurrentUser | null>(null);
export const authLoaded = ref(false);

export async function loadCurrentUser(): Promise<void> {
  try {
    currentUser.value = await fetchMe();
  } catch {
    currentUser.value = null;
  } finally {
    authLoaded.value = true;
  }
}

export function clearCurrentUser(): void {
  currentUser.value = null;
}
