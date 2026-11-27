import { STORE_KEYS, DEFAULT_UI_PREFS } from './config.js';

export const Store = {
  KEYS: STORE_KEYS,

  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  },

  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      // ពេល Storage ពេញ -> លុប cache ក្រាហ្វិកចាស់ៗចោលហើយសាករក្សាទុកម្តងទៀត
      try {
        Object.keys(localStorage).forEach((k) => {
          if (k.startsWith(STORE_KEYS.CACHE_PREFIX)) {
            localStorage.removeItem(k);
          }
        });
        localStorage.setItem(key, JSON.stringify(value));
        return true;
      } catch {
        return false;
      }
    }
  },

  remove(key) {
    try {
      localStorage.removeItem(key);
    } catch {}
  },

  getPrefs() {
    const saved = this.get(STORE_KEYS.PREFS, null);
    return { ...DEFAULT_UI_PREFS, ...(saved && typeof saved === 'object' ? saved : {}) };
  },

  savePrefs(prefs) {
    return this.set(STORE_KEYS.PREFS, prefs);
  },
};