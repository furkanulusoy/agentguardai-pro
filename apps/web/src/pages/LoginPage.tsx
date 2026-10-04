import { AuthShell } from '../components/AuthShell'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { apiErrorMessage } from '../api/client'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(apiErrorMessage(err, 'Login failed'))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthShell>
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center">
          <img src="/logo-mark.png" alt="AgentGuard" className="mb-3 h-11 w-11" />
          <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-50">
            AgentGuard'a giriş yap
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Hesabınıza erişmek için giriş yapın</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-800"
        >
          {error && (
            <div className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400">
              {error}
            </div>
          )}

          <label className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="email">
            E-posta
          </label>
          <input
            autoComplete="email"
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mb-4 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
            placeholder="you@company.com"
          />

          <label className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="password">
            Şifre
          </label>
          <input
            autoComplete="current-password"
            id="password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mb-6 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
            placeholder="••••••••"
          />

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full cursor-pointer rounded-lg bg-teal-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors duration-150 hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-teal-300"
          >
            {isSubmitting ? 'Giriş yapılıyor...' : 'Giriş yap'}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-slate-500 dark:text-slate-400">
          Hesabınız yok mu?{' '}
          <Link to="/register" className="font-medium text-teal-600 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-300">
            Kayıt olun
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
