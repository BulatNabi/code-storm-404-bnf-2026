'use client';

import Link from 'next/link';
import Navbar from '@/components/Navbar';
import { useLang } from '@/lib/lang-context';

export default function LogoutPage() {
  const { t } = useLang();
  return (
    <div className="page">
      <Navbar variant="public" />
      <div className="page-center">
        <div style={{ textAlign: 'center', maxWidth: 480 }}>
          <div style={{
            width: 72, height: 72, borderRadius: '50%',
            background: 'var(--surface)', border: '1px solid var(--border)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 28px', fontSize: '1.8rem',
          }}>👋</div>
          <h1 style={{ fontSize: '2rem', marginBottom: 12 }}>{t('logout.title')}</h1>
          <p style={{ color: 'var(--text-muted)', lineHeight: 1.8, marginBottom: 40 }}>
            {t('logout.subtitle')}
          </p>
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
            <Link href="/login" className="btn btn-primary btn-lg">{t('logout.btn_login')}</Link>
            <Link href="/" className="btn btn-ghost btn-lg">{t('logout.btn_home')}</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
