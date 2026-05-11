import Link from 'next/link';
import Navbar from '@/components/Navbar';

export default function LogoutPage() {
  return (
    <div className="page">
      <Navbar variant="public" />
      <div className="page-center">
        <div style={{ textAlign: 'center', maxWidth: 480 }}>
          {/* Icon */}
          <div style={{
            width: 72,
            height: 72,
            borderRadius: '50%',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 28px',
            fontSize: '1.8rem',
          }}>
            👋
          </div>

          <h1 style={{ fontSize: '2rem', marginBottom: 12 }}>You&apos;ve been signed out</h1>
          <p style={{ color: 'var(--text-muted)', lineHeight: 1.8, marginBottom: 40 }}>
            Thanks for using FeatureAI. Your session has ended and your tokens
            have been cleared. Come back any time to continue analyzing features.
          </p>

          <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
            <Link href="/login" className="btn btn-primary btn-lg">Log back in</Link>
            <Link href="/" className="btn btn-ghost btn-lg">Go home</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
