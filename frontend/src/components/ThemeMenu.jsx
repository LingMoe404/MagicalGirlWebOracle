import React from 'react';
import {
  Button,
  Menu,
  MenuItemRadio,
  MenuList,
  MenuPopover,
  MenuTrigger,
} from '@fluentui/react-components';
import { DesktopMac20Regular, WeatherMoon20Regular, WeatherSunny20Regular } from '@fluentui/react-icons';
import { useI18n } from '../i18n.jsx';
import { useThemeMode } from '../theme.jsx';

export default function ThemeMenu() {
  const { t } = useI18n();
  const { mode, setMode } = useThemeMode();
  const icon = mode === 'dark'
    ? <WeatherMoon20Regular />
    : mode === 'light' ? <WeatherSunny20Regular /> : <DesktopMac20Regular />;

  return <Menu>
    <MenuTrigger disableButtonEnhancement>
      <Button appearance="subtle" icon={icon} aria-label={t('theme.toggleAria')} />
    </MenuTrigger>
    <MenuPopover>
      <MenuList>
        <MenuItemRadio name="theme" value="light" checked={mode === 'light'} onClick={() => setMode('light')}>{t('theme.light')}</MenuItemRadio>
        <MenuItemRadio name="theme" value="dark" checked={mode === 'dark'} onClick={() => setMode('dark')}>{t('theme.dark')}</MenuItemRadio>
        <MenuItemRadio name="theme" value="system" checked={mode === 'system'} onClick={() => setMode('system')}>{t('theme.system')}</MenuItemRadio>
      </MenuList>
    </MenuPopover>
  </Menu>;
}
