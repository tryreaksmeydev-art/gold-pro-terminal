import { SYSTEM_QUESTIONS } from '../core/config.js';
import { Store } from '../core/storage.js';
import { $, escHtml, formatBold } from '../core/utils.js';
import { apiService } from '../services/apiService.js';
import { socketService } from '../services/socketService.js';

class AiChatWidget {
  constructor() {
    const saved = Store.get(Store.KEYS.CHAT, []);
    this.history = Array.isArray(saved) ? saved : [];
    this.visible = false;
    this.isBusy = false;
    this.noticeTimer = null;
  }

  init(prefs = {}) {
    this.setVisible(Boolean(prefs.showAI), false, false);
    this.renderHistory();
  }

  setVisible(flag, scroll = true, persist = true) {
    this.visible = Boolean(flag);
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.showAI = this.visible;
      Store.savePrefs(prefs);
    }

    if ($('chkAI')) $('chkAI').checked = this.visible;
    const sec = $('aiSection');
    sec?.classList.toggle('hidden', !this.visible);

    if (this.visible && scroll && sec) {
      setTimeout(() => sec.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
    } else if (!this.visible) {
      this.hideDropdown();
    }
  }

  renderQuestionList(list) {
    const box = $('questionList');
    if (!box) return;
    box.innerHTML = list.length
      ? list.map((q) => `<button type="button" data-qid="${q.id}" class="w-full text-left px-3.5 py-2.5 hover:bg-yellow-400/10 flex items-center justify-between gap-2 transition"><span class="text-xs text-gray-100">${escHtml(q.text)}</span><span class="shrink-0 text-[9px] px-1.5 py-0.5 rounded bg-white/5 text-yellow-300 border border-white/10 font-semibold">${escHtml(q.tag)}</span></button>`).join('')
      : '<div class="px-3.5 py-3 text-[11px] text-amber-300">មិនមានសំណួរត្រូវគ្នាក្នុងប្រព័ន្ធទេ។</div>';

    box.querySelectorAll('[data-qid]').forEach((btn) => {
      btn.addEventListener('click', () => this.selectQuestion(btn.dataset.qid));
    });
  }

  openDropdown() {
    $('questionDropdown')?.classList.remove('hidden');
    this.filterQuestions($('aiInput')?.value || '');
  }

  hideDropdown() {
    $('questionDropdown')?.classList.add('hidden');
  }

  toggleDropdown() {
    $('questionDropdown')?.classList.contains('hidden') ? this.openDropdown() : this.hideDropdown();
  }

  filterQuestions(query) {
    const s = String(query || '').trim().toLowerCase();
    $('politeNotice')?.classList.add('hidden');
    const filtered = !s
      ? SYSTEM_QUESTIONS
      : SYSTEM_QUESTIONS.filter((i) => i.text.toLowerCase().includes(s) || i.tag.toLowerCase().includes(s));
    this.renderQuestionList(filtered);
  }

  _showPoliteNotice() {
    const n = $('politeNotice');
    const inp = $('aiInput');
    n?.classList.remove('hidden');
    if (inp) {
      inp.classList.remove('input-shake');
      void inp.offsetWidth;
      inp.classList.add('input-shake');
    }
    this.renderQuestionList(SYSTEM_QUESTIONS);
    $('questionDropdown')?.classList.remove('hidden');
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => n?.classList.add('hidden'), 6500);
  }

  selectQuestion(id) {
    const item = SYSTEM_QUESTIONS.find((q) => q.id === id);
    if (!item) return;
    if ($('aiInput')) $('aiInput').value = '';
    $('politeNotice')?.classList.add('hidden');
    this.hideDropdown();
    this.ask(item.text, item.id);
  }

  handleSubmit(e) {
    e.preventDefault();
    if (this.isBusy) return;
    const val = ($('aiInput')?.value || '').trim();
    if (!val) {
      this.openDropdown();
      return;
    }
    const match = SYSTEM_QUESTIONS.find((q) => q.text.toLowerCase() === val.toLowerCase());
    match ? this.selectQuestion(match.id) : this._showPoliteNotice();
  }

  renderHistory() {
    const box = $('aiMessages');
    if (!box) return;
    if (!this.history.length) {
      box.innerHTML = `<div class="bg-white/5 border border-white/10 rounded-2xl rounded-tl-sm p-3.5 text-gray-200 space-y-1"><div class="text-yellow-400 font-bold text-xs">TRI PISAL AI V10.6 រួចរាល់សម្រាប់វិភាគ</div><p class="text-xs text-gray-300">ចុចលើប្រអប់ខាងក្រោម ដើម្បីជ្រើសរើសសំណួរវិភាគទាំង ១១ មុខ។</p></div>`;
      return;
    }
    box.innerHTML = this.history.map((item) => (
      item.role === 'user'
        ? `<div class="ml-8 bg-yellow-400 text-[#080c14] font-semibold rounded-2xl rounded-tr-sm px-3.5 py-2.5 shadow">${escHtml(item.text)}</div>`
        : `<div class="mr-2 bg-slate-900/90 border border-white/10 rounded-2xl rounded-tl-sm p-3 text-gray-200 space-y-1.5"><div class="flex items-center justify-between gap-2 border-b border-white/10 pb-1.5"><span class="font-bold text-yellow-400 text-xs">${escHtml(item.title)}</span><span class="text-[10px] px-1.5 py-0.5 rounded bg-yellow-400/15 text-yellow-300 font-bold border border-yellow-400/25">${escHtml(item.badge || '')}</span></div><ul class="space-y-1 list-disc list-inside text-xs">${(item.lines || []).map((l) => `<li>${formatBold(l)}</li>`).join('')}</ul></div>`
    )).join('');
    box.scrollTop = box.scrollHeight;
  }

  clearHistory() {
    this.history = [];
    Store.set(Store.KEYS.CHAT, []);
    this.renderHistory();
  }

  async ask(text, preset) {
    if (this.isBusy || !preset) return;
    this.isBusy = true;
    if ($('aiSendBtn')) $('aiSendBtn').disabled = true;

    this.history.push({ role: 'user', text });
    Store.set(Store.KEYS.CHAT, this.history.slice(-24));
    this.renderHistory();

    const box = $('aiMessages');
    const ld = document.createElement('div');
    ld.className = 'mr-6 bg-white/5 border border-white/10 rounded-2xl rounded-tl-sm p-3 text-yellow-300 text-xs';
    ld.textContent = 'កំពុងវិភាគខួរក្បាល V10.6, Bullish/Bearish OB, Exit Shield និងទិន្នន័យជាក់ស្តែង...';
    box?.appendChild(ld);
    if (box) box.scrollTop = box.scrollHeight;

    try {
      const data = await apiService.askAI({
        message: text,
        interval: socketService.getInterval(),
        preset,
      });
      this.history.push({ role: 'ai', title: data.title, badge: data.badge, lines: data.lines });
      Store.set(Store.KEYS.CHAT, this.history.slice(-24));
    } catch {
      this.history.push({
        role: 'ai',
        title: 'ការភ្ជាប់យឺតបណ្តោះអាសន្ន',
        badge: 'RETRY',
        lines: ['សូមពិនិត្យអ៊ីនធឺណិត រួចសាកល្បងចុចម្តងទៀត។'],
      });
    } finally {
      this.isBusy = false;
      if ($('aiSendBtn')) $('aiSendBtn').disabled = false;
      this.renderHistory();
    }
  }
}

export const aiChatWidget = new AiChatWidget();