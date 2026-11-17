import React, { useState } from 'react';
import {
  Button,
  Card,
  CardHeader,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Spinner,
  Text,
  Title2,
  makeStyles,
  tokens,
} from '@fluentui/react-components';
import { Key20Regular } from '@fluentui/react-icons';
import icon512 from '../assets/icon-512.png';
import iconObsidian from '../assets/icon-obsidian-512.png';
import LanguageSwitcher from './LanguageSwitcher.jsx';
import ThemeMenu from './ThemeMenu.jsx';
import { useI18n } from '../i18n.jsx';
import { useThemeMode } from '../theme.jsx';

const useStyles = makeStyles({
  page: {
    minHeight: '100vh',
    display: 'grid',
    placeItems: 'center',
    boxSizing: 'border-box',
    padding: tokens.spacingVerticalXXL,
    backgroundColor: tokens.colorNeutralBackground2,
    backgroundImage: `radial-gradient(1100px 560px at 12% 8%, ${tokens.colorPalettePurpleBackground2}, transparent 58%), radial-gradient(900px 520px at 88% 92%, ${tokens.colorBrandBackground2}, transparent 58%)`,
  },
  card: {
    width: 'min(450px, 100%)',
    padding: tokens.spacingVerticalXXL,
    rowGap: tokens.spacingVerticalL,
    borderRadius: tokens.borderRadiusXLarge,
    boxShadow: tokens.shadow16,
  },
  icon: {
    width: '92px',
    height: '92px',
    borderRadius: tokens.borderRadiusXLarge,
    objectFit: 'contain',
    boxShadow: tokens.shadow8,
  },
  title: {
    display: 'block',
    textAlign: 'center',
  },
  subtitle: {
    display: 'block',
    textAlign: 'center',
    color: tokens.colorNeutralForeground2,
  },
  form: {
    display: 'grid',
    rowGap: tokens.spacingVerticalM,
  },
  submit: {
    width: '100%',
    marginTop: tokens.spacingVerticalXS,
  },
  footer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    paddingTop: tokens.spacingVerticalS,
    borderTop: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
  },
  note: {
    display: 'block',
    textAlign: 'center',
    color: tokens.colorNeutralForeground3,
  },
});

export default function Login({ onLogin, loading, error }) {
  const { t } = useI18n();
  const { isDark } = useThemeMode();
  const styles = useStyles();
  const [value, setValue] = useState('');

  async function submit(event) {
    event.preventDefault();
    if (await onLogin(value.trim())) setValue('');
  }

  return <section id="login" className={styles.page}>
    <Card appearance="filled-alternative" size="large" className={styles.card}>
      <CardHeader
        image={<img className={styles.icon} src={isDark ? iconObsidian : icon512} alt="" />}
        header={<Title2 as="h1" className={styles.title}>MagicalGirlWebOracle</Title2>}
        description={<Text className={styles.subtitle}>{t('login.subtitle')}</Text>}
      />
      <form className={styles.form} onSubmit={event => event.preventDefault()} noValidate>
        <Field label={t('login.token')} required>
          <Input id="admin-token" type="password" contentBefore={<Key20Regular />} autoComplete="off"
            value={value} onChange={(_, data) => setValue(data.value)} maxLength={512} disabled={loading}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault();
                void submit(event);
              }
            }} />
        </Field>
        {error ? <MessageBar intent="error" politeness="assertive"><MessageBarBody>{error}</MessageBarBody></MessageBar> : null}
        <Button appearance="primary" size="large" type="button" className={styles.submit}
          disabled={!value.trim() || loading}
          icon={loading ? <Spinner size="tiny" /> : undefined}
          onClick={submit}>
          {loading ? t('login.submitting') : t('login.submit')}
        </Button>
      </form>
      <div className={styles.footer}>
        <LanguageSwitcher />
        <ThemeMenu />
      </div>
      <Text size={200} className={styles.note}>{t('login.memoryNote')}</Text>
    </Card>
  </section>;
}
