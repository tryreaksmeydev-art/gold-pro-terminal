async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: ctrl.signal });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return await res.json();
  } finally {
    clearTimeout(tid);
  }
}

export const apiService = {
  async fetchGold(interval) {
    const tf = encodeURIComponent(interval || '15m');
    const data = await fetchJsonWithTimeout(`/api/gold?interval=${tf}`, {}, 10000);
    if (!data || data.error) {
      throw new Error(data?.error || 'Invalid gold payload');
    }
    return data;
  },

  async askAI({ message, interval, preset }) {
    return fetchJsonWithTimeout(
      '/api/ai-chat',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, interval, preset }),
      },
      15000
    );
  },
};