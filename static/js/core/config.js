export const TF_SECONDS = Object.freeze({
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '4h': 14400,
  '1d': 86400,
});

export const BROKER_COMM_PER_LOT = 7.0; // $7/Lot round-turn commission
export const COMM_PIPS = 0.7;           // 0.7 pips លើ XAU/USD
export const MAX_TPSL_SLOTS = 3;

export const STORE_KEYS = Object.freeze({
  TF: 'tp_tf_v16',
  CHAT: 'tp_chat_v16',
  CALC: 'tp_calc_v16',
  ALERTS: 'tp_alerts_v16',
  JOURNAL: 'tp_journal_v16',
  PREFS: 'tp_prefs_v16',
  TPSL: 'tp_sl_trackers_v1',
  CACHE_PREFIX: 'tp_data_v16_',
});

export const DEFAULT_UI_PREFS = Object.freeze({
  showAI: false,
  showSignals: true,
  showZones: true,
  showSlTp: true,
  showInst: false,
  showScanner: false,
  showAudit: false,
  wakeLock: false,
  auditLot: 0.01,
  hudCollapsed: typeof window !== 'undefined' ? window.innerWidth < 640 : true,
});

export const DEFAULT_CALC_PREFS = Object.freeze({
  balance: 1000,
  risk: 1,
});

export const SYSTEM_QUESTIONS = Object.freeze([
  { id: 'brain_v8', text: 'ពិនិត្យសេចក្តីសម្រេចខួរក្បាល AI V10 & ព័ត៌មានសង្គ្រាម/ពិភពលោក', tag: 'AI V10' },
  { id: 'mtf_check', text: 'ផ្ទៀងផ្ទាត់ 4 Timeframes (15m, 1H, 4H, 1D)', tag: 'MTF' },
  { id: 'news_check', text: 'ពិនិត្យព័ត៌មានសេដ្ឋកិច្ចអាមេរិក (USD News)', tag: 'News' },
  { id: 'inst_levels', text: 'តំបន់ PDH, PDL និង Asian Session High/Low', tag: 'Institutional' },
  { id: 'entry', text: 'វិភាគចំណុចចូល Buy ឬ Sell ឥឡូវនេះ', tag: 'ចំណុចចូល' },
  { id: 'risk_plan', text: 'កំណត់ Stop Loss និង Take Profit ត្រង់ណា?', tag: 'SL / TP' },
  { id: 'deep_scan', text: 'ស្កេនសូចនាករ 5-in-1 ជាក់ស្តែង', tag: 'សូចនាករ' },
  { id: 'trend', text: 'វិភាគនិន្នាការទីផ្សារមាសឥឡូវនេះ', tag: 'និន្នាការ' },
  { id: 'pattern', text: 'វិភាគទម្រង់ទៀន Price Action ចុងក្រោយ', tag: 'ទម្រង់ទៀន' },
  { id: 'volatility', text: 'វាយតម្លៃហានិភ័យ ATR និងវគ្គទីផ្សារ', tag: 'ហានិភ័យ' },
  { id: 'summary', text: 'សង្ខេបស្ថានភាពទីផ្សារមាសពេញលេញ', tag: 'សង្ខេបរួម' },
]);