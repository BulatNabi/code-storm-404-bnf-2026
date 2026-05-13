'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { apiLogout } from '@/lib/api';
import { useLang, Locale } from '@/lib/lang-context';

type NavbarVariant = 'public' | 'login' | 'register' | 'app';

interface NavbarProps {
  variant: NavbarVariant;
}

const LANGUAGES: { code: Locale; label: string }[] = [
  { code: 'en', label: 'EN' },
  { code: 'ru', label: 'RU' },
  { code: 'uz', label: "O'Z" },
];

function LanguageSwitcher() {
  const { locale, setLocale } = useLang();

  return (
    <div style={{ position: 'relative' }}>
      <select
        value={locale}
        onChange={(e) => setLocale(e.target.value as Locale)}
        style={{
          padding: '8px 12px',
          borderRadius: 8,
          border: '1px solid var(--border)',
          background: 'var(--surface)',
          color: 'var(--text)',
          cursor: 'pointer',
          fontSize: '0.75rem',
          fontWeight: 600,
          fontFamily: 'Syne, sans-serif',
          letterSpacing: '0.04em',
          outline: 'none',
        }}
      >
        <option disabled value="">
          Lang
        </option>

        {LANGUAGES.map((lang) => (
          <option key={lang.code} value={lang.code}>
            {lang.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export default function Navbar({ variant }: NavbarProps) {
  const router = useRouter();
  const { t } = useLang();

  async function handleLogout() {
    try {
      await apiLogout();
    } catch (_) {}
    finally {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      router.push('/logout');
    }
  }

  return (
    <nav className="navbar">
      <Link href="/" style={{ display: 'flex', alignItems: 'center' }}>
        <img
          src="/themis-wordmark-dark.svg"
          alt="Logo"
          style={{ height: 20, width: 'auto' }}
        />
      </Link>
      <div className="navbar-actions">
        <LanguageSwitcher />
        {variant === 'public' && (
          <>
            <Link href="/login" className="btn btn-ghost">{t('nav.login')}</Link>
            <Link href="/register" className="btn btn-primary">{t('nav.register')}</Link>
          </>
        )}
        {variant === 'login' && (
          <Link href="/register" className="btn btn-primary">{t('nav.register')}</Link>
        )}
        {variant === 'register' && (
          <Link href="/login" className="btn btn-ghost">{t('nav.login')}</Link>
        )}
        {variant === 'app' && (
          <button className="btn btn-ghost" onClick={handleLogout}>
            {t('nav.logout')}
          </button>
        )}
      </div>
    </nav>
  );
}
