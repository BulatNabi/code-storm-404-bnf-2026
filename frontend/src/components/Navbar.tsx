'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { apiLogout } from '@/lib/api';

type NavbarVariant = 'public' | 'login' | 'register' | 'app';

interface NavbarProps {
  variant: NavbarVariant;
}

export default function Navbar({ variant }: NavbarProps) {
  const router = useRouter();

  async function handleLogout() {
    try {
      await apiLogout();
    } catch (_) {
      // ignore — clear tokens regardless
    } finally {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      router.push('/logout');
    }
  }

  return (
    <nav className="navbar">
      <Link href="/" className="navbar-logo">
        FeatureAI
      </Link>
      <div className="navbar-actions">
        {variant === 'public' && (
          <>
            <Link href="/login" className="btn btn-ghost">Log in</Link>
            <Link href="/register" className="btn btn-primary">Register</Link>
          </>
        )}
        {variant === 'login' && (
          <Link href="/register" className="btn btn-primary">Register</Link>
        )}
        {variant === 'register' && (
          <Link href="/login" className="btn btn-ghost">Log in</Link>
        )}
        {variant === 'app' && (
          <button className="btn btn-ghost" onClick={handleLogout}>
            Log out
          </button>
        )}
      </div>
    </nav>
  );
}
