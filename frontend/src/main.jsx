import React from 'react';
import { createRoot } from 'react-dom/client';
import { RendererProvider, createDOMRenderer } from '@fluentui/react-components';
import App from './App.jsx';
import { I18nProvider } from './i18n.jsx';
import { ThemeProvider } from './theme.jsx';
import './styles.css';

const nonce = document.querySelector('meta[name="csp-nonce"]')?.content;
const renderer = createDOMRenderer(document, { styleElementAttributes: nonce ? { nonce } : {} });

createRoot(document.getElementById('root')).render(
  <RendererProvider renderer={renderer}>
    <ThemeProvider>
      <I18nProvider>
        <App />
      </I18nProvider>
    </ThemeProvider>
  </RendererProvider>,
);
