import React from 'react';
import { Select } from '@fluentui/react-components';
import { LOCALES, useI18n } from '../i18n.jsx';

export default function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();
  return <Select
    className="language-switcher"
    size="small"
    value={locale}
    aria-label={t('common.language')}
    onChange={(_, event) => setLocale(event.value)}
  >
    {LOCALES.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
  </Select>;
}
