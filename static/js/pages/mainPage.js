import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, WakeLockManager } from '../core/utils.js';
import { socketService } from '../services/socketService.js';
import { chartEngine } from '../chart/chartEngine.js';
import { toastManager } from '../components/toastManager.js';
import { hudWidget } from '../components/hudWidget.js';
import { aiRadarWidget } from '../components/aiRadarWidget.js';
import { auditScorecard } from '../components/auditScorecard.js';
import { tpslTracker } from '../components/tpslTracker.js';
import { newsPanel } from '../components/newsPanel.js';
import { aiChatWidget } from '../components/aiChatWidget.js';
import { alertsJournal } from '../components/alertsJournal.js';
import { networkGuard } from '../components/networkGuard.js';
import { liveCommandBar } from '../components/liveCommandBar.js';
import { expertAnalystModal } from '../components/expertAnalystModal.js';

const wakeLock = new WakeLockManager('wakeLockBtn', 'wakeLockText');
let tickFlashTimer = null;

function setActiveTfButtons(tf) {
  document.querySelectorAll('.tf-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tf === tf);
  });
}

function updateConnectionUI(status) {
  const offlineBar = $('offlineBar');
  const wsStatus = $('wsStatus');
  const wsDot = $('wsDot');

  if (status === 'OFFLINE') {
    offlineBar?.classList.remove('hidden');
    if (wsStatus) wsStatus.textContent = 'OFFLINE';
    if (wsDot) wsDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-rose-500';
  } else if (status === 'TURBO') {
    offlineBar?.classList.add('hidden');
    if (wsStatus) wsStatus.textContent = 'TURBO';
    if (wsDot) wsDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-emerald-500';
  } else {
    offlineBar?.classList.add('hidden');
    if (wsStatus) wsStatus.textContent = 'SYNC';
    if (wsDot) wsDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-yellow-500';
  }
}

function updateHeaderPrice({ price, change, changePct, direction, bid, ask, spread }) {
  if (!Number.isFinite(price) || price <= 0) return;
  const fmt = `$${price.toFixed(2)}`;
  const liveEl = $('livePrice');
  if (liveEl) {
    liveEl.textContent = fmt;
    if (direction !== 0) {
      liveEl.style.color = direction > 0 ? '#00e5a8' : '#ff5c7c';
      clearTimeout(tickFlashTimer);
      tickFlashTimer = setTimeout(() => { liveEl.style.color = '#f9fafb'; }, 130);
    }
  }
  if ($('fsPrice')) $('fsPrice').textContent = fmt;

  if (typeof change === 'number' && Number.isFinite(change)) {
    const sign = change >= 0 ? '+' : '';
    const chEl = $('priceChange');
    if (chEl) {
      chEl.textContent = `${sign}${change.toFixed(2)} (${sign}${(changePct || 0).toFixed(2)}%)`;
      chEl.className = `text-[10.5px] sm:text-xs font-semibold tabular-nums ${change >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
    }
  }

  if (bid && ask && $('bidAskText')) {
    $('bidAskText').textContent = `Bid $${Number(bid).toFixed(2)} • Ask $${Number(ask).toFixed(2)} (Spd $${(Number(spread) || 0).toFixed(2)})`;
  }
}

function updateHeaderMeta(data, fromCache) {
  if ($('dataSource')) {
    $('dataSource').textContent = fromCache
      ? `${data.source || 'XAU/USD'} • ក្នុងទូរស័ព្ទ`
      : `${data.source || 'XAU/USD'} • Turbo`;
  }

  const fb = $('feedBadge');
  if (fb) {
    const isFx = Boolean(data.is_forex);
    fb.textContent = isFx ? 'FOREX' : (data.is_spot ? 'SPOT' : 'FUTURES');
    fb.className = isFx
      ? 'hidden md:inline-block text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 font-bold border border-emerald-500/25 shrink-0'
      : 'hidden md:inline-block text-[9px] px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 font-bold border border-sky-500/25 shrink-0';
  }

  if (data.session?.name && $('sessionText')) {
    $('sessionText').textContent = data.session.name;
  }
}

function bindBusListeners() {
  bus.on('market:loading', ({ loading }) => {
    const loader = $('loader');
    if (loader) loader.style.display = loading ? 'flex' : 'none';
  });

  bus.on('market:full', ({ data, fromCache }) => {
    updateHeaderMeta(data, fromCache);
  });

  bus.on('price:updated', (tick) => updateHeaderPrice(tick));
  bus.on('connection:status', ({ status }) => updateConnectionUI(status));
  bus.on('interval:changed', ({ interval }) => setActiveTfButtons(interval));
}

function bindDomEvents(prefs) {
  // 1) Wake Lock (រក្សាអេក្រង់ទូរស័ព្ទមិនឱ្យរលត់)
  $('wakeLockBtn')?.addEventListener('click', async () => {
    if (!wakeLock.isSupported()) {
      toastManager.showPopup({
        type: 'warn',
        badge: 'WAKE LOCK',
        title: 'កម្មវិធីរុករកមិនគាំទ្រ Stay On ទេ',
        body: 'សូមប្រើ Chrome / Safari ជំនាន់ថ្មីលើ HTTPS ដើម្បីរក្សាអេក្រង់កុំឱ្យរលត់។',
        playSound: false,
        autoHideMs: 5000,
      });
      return;
    }
    const nextPrefs = Store.getPrefs();
    nextPrefs.wakeLock = !nextPrefs.wakeLock;
    Store.savePrefs(nextPrefs);
    await wakeLock.setEnabled(nextPrefs.wakeLock);
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Store.getPrefs().wakeLock) {
      wakeLock.acquire();
    }
  });

  // 2) Timeframe Buttons
  document.querySelectorAll('.tf-btn').forEach((btn) => {
    btn.addEventListener('click', () => socketService.switchInterval(btn.dataset.tf));
  });

  // 3) Control Chips (Checkboxes)
  const chkSignals = $('chkSignals');
  if (chkSignals) {
    chkSignals.checked = prefs.showSignals !== false;
    chkSignals.addEventListener('change', () => {
      const p = Store.getPrefs();
      p.showSignals = chkSignals.checked;
      Store.savePrefs(p);
      chartEngine.setOverlayOptions({ showSignals: p.showSignals });
    });
  }

  const chkZones = $('chkZones');
  if (chkZones) {
    chkZones.checked = prefs.showZones !== false;
    chkZones.addEventListener('change', () => {
      const p = Store.getPrefs();
      p.showZones = chkZones.checked;
      Store.savePrefs(p);
      chartEngine.setOverlayOptions({ showZones: p.showZones });
    });
  }

  const chkSlTp = $('chkSlTp');
  if (chkSlTp) {
    chkSlTp.checked = prefs.showSlTp !== false;
    chkSlTp.addEventListener('change', () => hudWidget.setVisible(chkSlTp.checked));
  }

  $('chkScanner')?.addEventListener('change', (e) => aiRadarWidget.setVisible(e.target.checked));
  $('chkAudit')?.addEventListener('change', (e) => auditScorecard.setVisible(e.target.checked, true));
  $('chkAI')?.addEventListener('change', (e) => aiChatWidget.setVisible(e.target.checked, true));

  // 4) Chart Toolbar & HUD Controls
  const instBtn = $('toggleInstBtn');
  if (instBtn) {
    instBtn.textContent = `PDH/Asian: ${prefs.showInst ? 'ON' : 'OFF'}`;
    instBtn.addEventListener('click', () => {
      const p = Store.getPrefs();
      p.showInst = !p.showInst;
      Store.savePrefs(p);
      instBtn.textContent = `PDH/Asian: ${p.showInst ? 'ON' : 'OFF'}`;
      chartEngine.setShowInst(p.showInst);
    });
  }

  $('btnOpenSignalPopup')?.addEventListener('click', () => toastManager.openLatestSignal());
  $('btnResetChart')?.addEventListener('click', () => chartEngine.resetView());
  $('fsBtn')?.addEventListener('click', () => chartEngine.toggleFullscreen());
  $('hudToggleBtn')?.addEventListener('click', (e) => hudWidget.toggleCollapse(e));
  $('hudReasonBox')?.addEventListener('click', () => toastManager.openLatestSignal());
  $('btnCloseRadar')?.addEventListener('click', () => aiRadarWidget.setVisible(false));

  // 5) Toasts & Floating Opportunity Pill
  $('liveOppJumpBtn')?.addEventListener('click', () => {
    toastManager.dismissOppPill();
    aiRadarWidget.setVisible(true);
    auditScorecard.setVisible(true, true);
  });
  $('liveOppCloseBtn')?.addEventListener('click', () => toastManager.dismissOppPill());
  $('alertToastCloseBtn')?.addEventListener('click', () => toastManager.dismissPriceAlert());
  $('sigToastCloseBtn')?.addEventListener('click', () => toastManager.dismissPopup());
  $('sigToastDismissBtn')?.addEventListener('click', () => toastManager.dismissPopup());
  $('sigToastTrackBtn')?.addEventListener('click', () => {
    toastManager.dismissPopup();
    tpslTracker.trackCurrentAiSignal();
  });

  // 6) Audit Scorecard (Lot 0.01 - 0.05)
  document.querySelectorAll('.lot-btn').forEach((btn) => {
    btn.addEventListener('click', () => auditScorecard.setLot(Number(btn.dataset.alot)));
  });
  $('btnCloseAudit')?.addEventListener('click', () => auditScorecard.setVisible(false));

  // 7) 3-Slot TP/SL Tracker Modal
  $('btnAddTpSl')?.addEventListener('click', () => tpslTracker.openModal(-1));
  $('btnCloseTpSlModal')?.addEventListener('click', () => tpslTracker.closeModal());
  $('msBtnBuy')?.addEventListener('click', () => tpslTracker.setModalSide('BUY'));
  $('msBtnSell')?.addEventListener('click', () => tpslTracker.setModalSide('SELL'));
  $('btnAutoFillAi')?.addEventListener('click', () => tpslTracker.autoFillFromAI());
  $('btnQuickSlTp36')?.addEventListener('click', () => tpslTracker.quickSlTp(3, 6));
  $('btnQuickSlTp510')?.addEventListener('click', () => tpslTracker.quickSlTp(5, 10));
  $('msDeleteBtn')?.addEventListener('click', () => tpslTracker.removeSelected());
  $('btnSaveTpSl')?.addEventListener('click', () => tpslTracker.save());

  // 8) Lot Calculator, Price Alerts & Trade Journal
  $('calcBalance')?.addEventListener('input', () => alertsJournal.updateLotCalc());
  $('calcRisk')?.addEventListener('input', () => alertsJournal.updateLotCalc());
  $('formPriceAlert')?.addEventListener('submit', (e) => alertsJournal.addAlert(e));
  $('formJournal')?.addEventListener('submit', (e) => alertsJournal.addJournalEntry(e));

  // 9) AI Copilot Chat
  $('btnClearChat')?.addEventListener('click', () => aiChatWidget.clearHistory());
  $('btnCloseChat')?.addEventListener('click', () => aiChatWidget.setVisible(false));
  $('btnHideQuestions')?.addEventListener('click', () => aiChatWidget.hideDropdown());
  $('btnToggleQuestions')?.addEventListener('click', () => aiChatWidget.toggleDropdown());
  $('aiInput')?.addEventListener('focus', () => aiChatWidget.openDropdown());
  $('aiInput')?.addEventListener('input', (e) => aiChatWidget.filterQuestions(e.target.value));
  $('formAiChat')?.addEventListener('submit', (e) => aiChatWidget.handleSubmit(e));
}

function bootstrap() {
  if (typeof window.lucide !== 'undefined') {
    window.lucide.createIcons();
  }

  const prefs = Store.getPrefs();
  const initialTf = socketService.getInterval();

  // Initialize Modules in Dependency Order
  chartEngine.init({ interval: initialTf, prefs });
  toastManager.init({ auditLot: prefs.auditLot });
  hudWidget.init(prefs);
  aiRadarWidget.init(prefs);
  auditScorecard.init(prefs);
  tpslTracker.init();
  newsPanel.init();
  aiChatWidget.init(prefs);
  alertsJournal.init();
  networkGuard.init();
  liveCommandBar.init(prefs);
  expertAnalystModal.init(prefs);

  bindBusListeners();
  bindDomEvents(prefs);
  setActiveTfButtons(initialTf);

  if (prefs.wakeLock) {
    wakeLock.setEnabled(true);
  } else {
    wakeLock.updateUI(false);
  }

  socketService.init();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap);
} else {
  bootstrap();
}