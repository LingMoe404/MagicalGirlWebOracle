import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import en from './locales/en.json';
import zhCN from './locales/zh-CN.json';
import zhTW from './locales/zh-TW.json';
import ja from './locales/ja.json';

export const LOCALES = [
  { id: 'en', label: 'English', intl: 'en-US' },
  { id: 'zh-CN', label: '简体中文', intl: 'zh-CN' },
  { id: 'zh-TW', label: '繁體中文', intl: 'zh-TW' },
  { id: 'ja', label: '日本語', intl: 'ja-JP' },
];

const DICTIONARIES = { en, 'zh-CN': zhCN, 'zh-TW': zhTW, ja };
const INTL = Object.fromEntries(LOCALES.map(item => [item.id, item.intl]));
// A non-sensitive locale preference is kept in a cookie so the admin bundle
// never touches localStorage/sessionStorage (credentials stay in memory).
const COOKIE = 'magicalgirl_web_oracle_locale';

function readLocaleCookie() {
  const prefix = `${COOKIE}=`;
  const entry = document.cookie.split('; ').find(item => item.startsWith(prefix));
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : '';
}

function writeLocaleCookie(locale) {
  document.cookie = `${COOKIE}=${encodeURIComponent(locale)}; Path=/; SameSite=Strict; Max-Age=31536000`;
}

function detectLocale() {
  const saved = readLocaleCookie();
  if (saved && DICTIONARIES[saved]) return saved;
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language || 'en'];
  for (const raw of languages) {
    const value = String(raw).toLowerCase();
    if (value.startsWith('zh')) {
      if (/(tw|hk|mo|hant)/.test(value)) return 'zh-TW';
      return 'zh-CN';
    }
    if (value.startsWith('ja')) return 'ja';
    if (value.startsWith('en')) return 'en';
  }
  return 'en';
}

function interpolate(template, params) {
  return template.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, name) => (
    params && params[name] != null ? String(params[name]) : match
  ));
}

const I18nContext = createContext(null);

export function I18nProvider({ children }) {
  const [locale, setLocale] = useState(detectLocale);

  useEffect(() => {
    writeLocaleCookie(locale);
    document.documentElement.lang = locale;
  }, [locale]);

  const t = useCallback((key, params) => {
    const dictionary = DICTIONARIES[locale] || DICTIONARIES.en;
    const template = dictionary[key] ?? DICTIONARIES.en[key] ?? key;
    return interpolate(template, params || {});
  }, [locale]);

  const intlLocale = INTL[locale] || INTL.en;

  const formatNumber = useCallback((value, options) => {
    if (value == null || value === '') return '—';
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return number.toLocaleString(intlLocale, { maximumFractionDigits: 6, ...(options || {}) });
  }, [intlLocale]);

  const formatDateTime = useCallback(value => (
    value == null ? '—' : new Date(value * 1000).toLocaleString(intlLocale)
  ), [intlLocale]);

  const formatTime = useCallback(value => (
    value == null ? '—' : new Date(value * 1000).toLocaleTimeString(intlLocale)
  ), [intlLocale]);

  const formatList = useCallback(values => {
    const list = (values || []).filter(Boolean);
    if (!list.length) return '';
    try {
      return new Intl.ListFormat(intlLocale, { style: 'short', type: 'conjunction' }).format(list);
    } catch {
      return list.join(', ');
    }
  }, [intlLocale]);

  const value = useMemo(() => ({
    locale, setLocale, t, intlLocale, formatNumber, formatDateTime, formatTime, formatList,
  }), [locale, setLocale, t, intlLocale, formatNumber, formatDateTime, formatTime, formatList]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside I18nProvider');
  return value;
}
