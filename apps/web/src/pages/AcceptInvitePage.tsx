import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { previewInvite } from '../api/auth'
import { apiErrorMessage } from '../api/client'
import { useAuth } from '../context/AuthContext'
import type { InvitePreview } from '../types'
import { Spinner } from '../components/ui'

export function AcceptInvitePage() {
  const { acceptInvite } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''

  const [preview, setPreview] = useState<InvitePreview | null>(null)
  // Derived once at mount, not set from an effect -- a missing token is
  // known synchronously from the URL, nothing to wait on.
  const [previewError, setPreviewError] = useState<string | null>(() =>
    token ? null : 'Davet linki eksik',
  )
  const [isLoadingPreview, setIsLoadingPreview] = useState(() => !!token)

  const [password, setPassword] = useState('')
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    if (!token) return
    let cancelled = false
    previewInvite(token)
      .then((data) => {
        if (!cancelled) setPreview(data)
      })
      .catch((err) => {
        if (!cancelled) setPreviewError(apiErrorMessage(err, 'Bu davet geçersiz veya süresi dolmuş'))
      })
      .finally(() => {
        if (!cancelled) setIsLoadingPreview(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitError(null)
    setIsSubmitting(true)
    try {
      await acceptInvite(token, password)
      navigate('/', { replace: true })
    } catch (err) {
      setSubmitError(apiErrorMessage(err, 'Davet kabul edilemedi'))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10 dark:bg-slate-900">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center">
          <img src="/logo-mark.png" alt="AgentGuard" className="mb-3 h-11 w-11" />
          <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-50">
            Davete katıl
          </h1>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-800">
          {isLoadingPreview ? (
            <div className="flex items-center justify-center py-8">
              <Spinner />
            </div>
          ) : previewError || !preview ? (
            <>
              <div className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400">
                {previewError ?? 'Bu davet geçersiz veya süresi dolmuş'}
              </div>
              <p className="text-center text-sm text-slate-500 dark:text-slate-400">
                <Link to="/login" className="font-medium text-teal-600 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-300">
                  Giriş sayfasına dön
                </Link>
              </p>
            </>
          ) : (
            <>
              <p className="mb-4 text-sm text-slate-600 dark:text-slate-300">
                <span className="font-semibold text-slate-900 dark:text-slate-100">{preview.tenant_name}</span>{' '}
                workspace'ine{' '}
                <span className="font-semibold text-slate-900 dark:text-slate-100">{preview.role}</span> rolüyle
                davet edildin.
              </p>

              <form onSubmit={handleSubmit}>
                {submitError && (
                  <div className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400">
                    {submitError}
                  </div>
                )}

                <label className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="email">
                  E-posta
                </label>
                <input
                  id="email"
                  type="email"
                  disabled
                  value={preview.email}
                  className="mb-4 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-400"
                />

                <label className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="password">
                  Şifre belirle
                </label>
                <input
                  id="password"
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="mb-6 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
                  placeholder="En az 8 karakter"
                />

                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="w-full cursor-pointer rounded-lg bg-teal-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors duration-150 hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-teal-300"
                >
                  {isSubmitting ? 'Katılınıyor...' : 'Davete katıl'}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
