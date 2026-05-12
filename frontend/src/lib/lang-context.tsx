'use client';

import { createContext, useContext, useState, useEffect, ReactNode, useCallback } from 'react';
import en from '@/messages/en.json';
import ru from '@/messages/ru.json';
import uz from '@/messages/uz.json';

export type Locale = 'en' | 'ru' | 'uz';

const messages: Record<Locale, typeof en> = { en, ru, uz };

interface LangContextType {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string) => string;
}

const LangContext = createContext<LangContextType>({
  locale: 'en',
  setLocale: () => {},
  t: (key) => key,
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>('en');

  useEffect(() => {
    const saved = localStorage.getItem('lang') as Locale | null;
    if (saved && ['en', 'ru', 'uz'].includes(saved)) {
      setLocaleState(saved);
    }
  }, []);

  function setLocale(l: Locale) {
    setLocaleState(l);
    localStorage.setItem('lang', l);
  }

  // Resolves dot-notation keys like "nav.login" → messages[locale].nav.login
  const t = useCallback(
    (key: string): string => {
      const parts = key.split('.');
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let value: any = messages[locale];
      for (const part of parts) {
        if (value == null) return key;
        value = value[part];
      }
      return typeof value === 'string' ? value : key;
    },
    [locale]
  );

  return (
    <LangContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </LangContext.Provider>
  );
}

export function useLang() {
  return useContext(LangContext);
}
