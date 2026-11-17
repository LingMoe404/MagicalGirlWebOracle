import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import {
  FluentProvider,
  createDarkTheme,
  createHighContrastTheme,
  createLightTheme,
} from '@fluentui/react-components';

// 16-stop brand ramp derived from the project icon's violet/gold palette.
// Stop 80 is the seed used for primary brand fills.
const brandRamp = {
  10: '#16022c',
  20: '#22053f',
  30: '#2e0953',
  40: '#3a0e68',
  50: '#48157d',
  60: '#571d93',
  70: '#6828aa',
  80: '#7b3ac2',
  90: '#8d4ed1',
  100: '#9f63dc',
  110: '#b07ae6',
  120: '#c092ee',
  130: '#cfabf4',
  140: '#ddc4f8',
  150: '#ebdcfb',
  160: '#f8f0ff',
};

export const oracleLightTheme = { ...createLightTheme(brandRamp) };
export const oracleDarkTheme = { ...createDarkTheme(brandRamp) };

// The generator cannot infer these: brand text needs more contrast than the
// brand fill on each surface.
oracleLightTheme.colorBrandForeground1 = brandRamp[70];
oracleLightTheme.colorBrandForegroundLink = brandRamp[70];
oracleDarkTheme.colorBrandForeground1 = brandRamp[110];
oracleDarkTheme.colorBrandForeground2 = brandRamp[120];
oracleDarkTheme.colorBrandForegroundLink = brandRamp[120];

const ThemeContext = createContext(null);
const COOKIE = 'magicalgirl_web_oracle_theme';
const MODES = new Set(['light', 'dark', 'system']);

function readModeCookie() {
  const prefix = `${COOKIE}=`;
  const entry = document.cookie.split('; ').find(item => item.startsWith(prefix));
  const value = entry ? decodeURIComponent(entry.slice(prefix.length)) : '';
  return MODES.has(value) ? value : 'system';
}

function writeModeCookie(mode) {
  document.cookie = `${COOKIE}=${encodeURIComponent(mode)}; Path=/; SameSite=Strict; Max-Age=31536000`;
}

function mediaMatches(query) {
  return typeof window !== 'undefined' && window.matchMedia(query).matches;
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(readModeCookie);
  const [prefersDark, setPrefersDark] = useState(() => mediaMatches('(prefers-color-scheme: dark)'));
  const [forcedColors, setForcedColors] = useState(() => mediaMatches('(forced-colors: active)'));

  useEffect(() => {
    const dark = window.matchMedia('(prefers-color-scheme: dark)');
    const contrast = window.matchMedia('(forced-colors: active)');
    const onDark = event => setPrefersDark(event.matches);
    const onContrast = event => setForcedColors(event.matches);
    dark.addEventListener('change', onDark);
    contrast.addEventListener('change', onContrast);
    return () => {
      dark.removeEventListener('change', onDark);
      contrast.removeEventListener('change', onContrast);
    };
  }, []);

  useEffect(() => {
    writeModeCookie(mode);
  }, [mode]);

  const isDark = mode === 'dark' || (mode === 'system' && prefersDark);
  const theme = useMemo(() => (
    forcedColors
      ? createHighContrastTheme(brandRamp)
      : isDark ? oracleDarkTheme : oracleLightTheme
  ), [forcedColors, isDark]);

  const value = useMemo(() => ({ mode, setMode, isDark, theme }), [mode, isDark, theme]);

  return <ThemeContext.Provider value={value}>
    <FluentProvider theme={theme} className="oracle-theme">
      {children}
    </FluentProvider>
  </ThemeContext.Provider>;
}

export function useThemeMode() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error('useThemeMode must be used inside ThemeProvider');
  return value;
}
