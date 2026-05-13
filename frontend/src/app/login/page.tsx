'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import Navbar from '@/components/Navbar';
import { apiLogin } from '@/lib/api';
import { useLang } from '@/lib/lang-context';

export default function LoginPage() {
  const router = useRouter();
  const { t } = useLang();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await apiLogin(email, password);
      localStorage.setItem('access_token', data.access_token);
      localStorage.setItem('refresh_token', data.refresh_token);
      router.push('/projects');
    } catch (err: any) {
      setError(err?.detail || t('login.error_default'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <Navbar variant="login" />
      <div className="page-center">
        <div style={{ width: '100%', maxWidth: 420 }}>
            {/* Header */}
          <div style={{ marginBottom: 32 }}>
            <h1 style={{ fontSize: '2rem', marginBottom: 8 }}>{t('login.title')}</h1>
            <p style={{ color: 'var(--text-muted)' }}>
              {t('login.subtitle')}{' '}
              <Link href="/register" style={{ color: 'var(--accent)' }}>{t('login.subtitle_link')}</Link>
            </p>
          </div>

          <div className="card">
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {error && <div className="alert alert-error">{error}</div>}
              <div className="form-group">
                <label className="form-label">{t('login.email')}</label>
                <input 
                className="form-input" 
                type="email" 
                placeholder={t('login.email_placeholder')}
                value={email} 
                onChange={e => setEmail(e.target.value)} 
                required 
                autoComplete="email" 
                />
              </div>
              <div className="form-group">
                <label className="form-label">{t('login.password')}</label>
                <input className="form-input" 
                type="password" 
                placeholder={t('login.password_placeholder')}
                value={password} 
                onChange={e => setPassword(e.target.value)} 
                required 
                autoComplete="current-password" 
                />
              </div>
              
              <button 
              type="submit" 
              className="btn btn-primary" 
              disabled={loading} 
              style={{ marginTop: 4 }}
              >
                {loading ? t('login.submitting') : t('login.submit')}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
