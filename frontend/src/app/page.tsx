'use client';

import Link from 'next/link';
import Navbar from '@/components/Navbar';
import { useLang } from '@/lib/lang-context';

export default function HomePage() {
  const { t } = useLang();
  return (
    <div className="page">
      <Navbar variant="public" />
      <div className="page-center" style={{ position: 'relative', zIndex: 1 }}>
        <div style={{ textAlign: 'center', maxWidth: 680 }}>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 32 }}>
            <span className="tag tag-accent">
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', display: 'inline-block' }} />
              {t('home.tag')}
            </span>
          </div>
          <h1 style={{ fontSize: 'clamp(2.4rem, 6vw, 4rem)', marginBottom: 24, letterSpacing: '-0.03em' }}>
            {t('home.title1')}{' '}
            <span style={{
              background: 'linear-gradient(135deg, var(--accent) 0%, #a78bfa 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}>
              {t('home.title2')}
            </span>
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '1.1rem', lineHeight: 1.8, marginBottom: 48, fontWeight: 300 }}>
            {t('home.subtitle')}
          </p>
          <div style={{ display: 'flex', gap: 16, justifyContent: 'center', flexWrap: 'wrap' }}>
            <Link href="/register" className="btn btn-primary btn-lg">{t('home.cta_primary')}</Link>
            <Link href="/login" className="btn btn-ghost btn-lg">{t('home.cta_secondary')}</Link>
          </div>
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap', marginTop: 64 }}>
            {(['home.pill1', 'home.pill2', 'home.pill3', 'home.pill4'] as const).map(key => (
              <span key={key} style={{
                padding: '6px 14px', borderRadius: '100px',
                border: '1px solid var(--border)', fontSize: '0.8rem', color: 'var(--text-muted)',
              }}>
                {t(key)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
