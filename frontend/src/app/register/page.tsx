'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import Navbar from '@/components/Navbar';
import { apiRegister } from '@/lib/api';

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState({ name: '', surname: '', email: '', password: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    setForm(prev => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await apiRegister(form);
      router.push('/login?registered=1');
    } catch (err: any) {
      setError(err?.detail || 'Registration failed. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <Navbar variant="register" />
      <div className="page-center">
        <div style={{ width: '100%', maxWidth: 460 }}>
          {/* Header */}
          <div style={{ marginBottom: 32 }}>
            <h1 style={{ fontSize: '2rem', marginBottom: 8 }}>Create your account</h1>
            <p style={{ color: 'var(--text-muted)' }}>
              Already have an account?{' '}
              <Link href="/login" style={{ color: 'var(--accent)' }}>Log in</Link>
            </p>
          </div>

          <div className="card">
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {error && <div className="alert alert-error">{error}</div>}

              {/* Name row */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div className="form-group">
                  <label className="form-label">First name</label>
                  <input
                    className="form-input"
                    type="text"
                    name="name"
                    placeholder="Alex"
                    value={form.name}
                    onChange={handleChange}
                    required
                    autoComplete="given-name"
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Last name</label>
                  <input
                    className="form-input"
                    type="text"
                    name="surname"
                    placeholder="Smith"
                    value={form.surname}
                    onChange={handleChange}
                    required
                    autoComplete="family-name"
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Email</label>
                <input
                  className="form-input"
                  type="email"
                  name="email"
                  placeholder="you@example.com"
                  value={form.email}
                  onChange={handleChange}
                  required
                  autoComplete="email"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Password</label>
                <input
                  className="form-input"
                  type="password"
                  name="password"
                  placeholder="Create a strong password"
                  value={form.password}
                  onChange={handleChange}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </div>

              <button
                type="submit"
                className="btn btn-primary"
                disabled={loading}
                style={{ marginTop: 4 }}
              >
                {loading ? 'Creating account…' : 'Create account'}
              </button>
            </form>
          </div>

          <p style={{ textAlign: 'center', marginTop: 20, fontSize: '0.8rem', color: 'var(--text-dim)' }}>
            By registering, you agree to our terms of service.
          </p>
        </div>
      </div>
    </div>
  );
}
