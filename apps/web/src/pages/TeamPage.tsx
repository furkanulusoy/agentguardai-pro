import { useEffect, useState, type FormEvent } from 'react'
import { Check, Copy, KeyRound, Mail, UserPlus, Users, X } from 'lucide-react'
import { createInvite, fetchInvites, fetchMembers, resetMemberPassword, revokeInvite } from '../api/tenant'
import { apiErrorMessage } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import type { Invitation, Member } from '../types'
import { Badge, Button, Card, EmptyState, PageHeader, Spinner } from '../components/ui'
import { MascotBird } from '../components/MascotBird'

const AVATAR_COLORS = [
  'bg-teal-100 text-teal-700 dark:bg-teal-500/15 dark:text-teal-300',
  'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300',
  'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300',
]

// Mirrors apps/api/routers/tenant.py's INVITABLE_ROLES -- OWNER excluded
// on purpose there (a mere tenant.admin must not be able to mint a peer
// owner), so it's excluded here too rather than offered and then 400'd.
const INVITABLE_ROLES = ['VIEWER', 'OPERATOR', 'APPROVER', 'ADMIN']

function avatarColor(email: string) {
  let hash = 0
  for (let i = 0; i < email.length; i++) hash = (hash * 31 + email.charCodeAt(i)) >>> 0
  return AVATAR_COLORS[hash % AVATAR_COLORS.length]
}

export function TeamPage() {
  const { user } = useAuth()
  const { showToast } = useToast()
  const [members, setMembers] = useState<Member[] | null>(null)
  const [invites, setInvites] = useState<Invitation[] | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const [showInviteForm, setShowInviteForm] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('OPERATOR')
  const [isInviting, setIsInviting] = useState(false)
  const [newInviteLink, setNewInviteLink] = useState<string | null>(null)
  const [linkCopied, setLinkCopied] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)

  const [resettingId, setResettingId] = useState<string | null>(null)
  const [resetLink, setResetLink] = useState<{ email: string; link: string } | null>(null)
  const [resetLinkCopied, setResetLinkCopied] = useState(false)

  // require_permission("tenant.admin") on the backend is what actually
  // enforces this -- this is only a UI hint so someone without the
  // permission doesn't see a button that would just 403. The OWNER-can't-
  // be-reset-by-a-mere-ADMIN rule (apps/api/routers/tenant.py) is enforced
  // the same way -- a stray click just gets a toast, not a silent no-op.
  const canInvite = user?.roles.some((r) => r === 'OWNER' || r === 'ADMIN') ?? false

  async function load() {
    try {
      const [membersData, invitesData] = await Promise.all([
        fetchMembers(),
        canInvite ? fetchInvites() : Promise.resolve([]),
      ])
      setMembers(membersData)
      setInvites(invitesData)
    } catch (err) {
      showToast(apiErrorMessage(err, 'Ekip üyeleri yüklenemedi'), 'error')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleInvite(e: FormEvent) {
    e.preventDefault()
    setIsInviting(true)
    try {
      const invite = await createInvite(inviteEmail.trim(), inviteRole)
      const link = `${window.location.origin}/accept-invite?token=${invite.invite_token}`
      setNewInviteLink(link)
      setLinkCopied(false)
      setInviteEmail('')
      await load()
    } catch (err) {
      showToast(apiErrorMessage(err, 'Davet oluşturulamadı'), 'error')
    } finally {
      setIsInviting(false)
    }
  }

  async function handleCopyLink() {
    if (!newInviteLink) return
    await navigator.clipboard.writeText(newInviteLink)
    setLinkCopied(true)
  }

  async function handleResetPassword(member: Member) {
    setResettingId(member.id)
    try {
      const { reset_token } = await resetMemberPassword(member.id)
      const link = `${window.location.origin}/reset-password?token=${reset_token}`
      setResetLink({ email: member.email, link })
      setResetLinkCopied(false)
    } catch (err) {
      showToast(apiErrorMessage(err, 'Şifre sıfırlama linki oluşturulamadı'), 'error')
    } finally {
      setResettingId(null)
    }
  }

  async function handleCopyResetLink() {
    if (!resetLink) return
    await navigator.clipboard.writeText(resetLink.link)
    setResetLinkCopied(true)
  }

  async function handleRevoke(id: string) {
    setRevokingId(id)
    try {
      await revokeInvite(id)
      showToast('Davet iptal edildi')
      await load()
    } catch (err) {
      showToast(apiErrorMessage(err, 'Davet iptal edilemedi'), 'error')
    } finally {
      setRevokingId(null)
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-8 py-10">
      <div className="mb-8 flex items-center justify-between">
        <PageHeader title="Ekip" description="Bu workspace'e erişimi olan üyeler ve rolleri." />
        <div className="flex items-center gap-3">
          {members && members.length > 0 && (
            <span className="rounded-full bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-700 dark:bg-teal-500/15 dark:text-teal-300">
              {members.length} üye
            </span>
          )}
          {canInvite && (
            <Button
              onClick={() => {
                setShowInviteForm((prev) => !prev)
                setNewInviteLink(null)
              }}
            >
              <UserPlus className="h-4 w-4" />
              Davet et
            </Button>
          )}
          <MascotBird />
        </div>
      </div>

      {canInvite && showInviteForm && (
        <Card className="mb-6 p-5">
          {newInviteLink ? (
            <div>
              <p className="mb-2 text-sm font-medium text-slate-900 dark:text-slate-100">
                Davet oluşturuldu -- bu linki davet ettiğin kişiye ilet
              </p>
              <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
                Bu linki şu an göstermemizin sebebi: henüz otomatik e-posta göndermiyoruz, link
                sadece bir kere gösteriliyor.
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={newInviteLink}
                  onFocus={(e) => e.target.select()}
                  className="flex-1 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300"
                />
                <Button variant="secondary" onClick={() => void handleCopyLink()}>
                  {linkCopied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  {linkCopied ? 'Kopyalandı' : 'Kopyala'}
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleInvite} className="flex items-end gap-3">
              <div className="flex-1">
                <label className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300" htmlFor="inviteEmail">
                  E-posta
                </label>
                <input
                  id="inviteEmail"
                  type="email"
                  required
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="teammate@company.com"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300" htmlFor="inviteRole">
                  Rol
                </label>
                <select
                  id="inviteRole"
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
                >
                  {INVITABLE_ROLES.map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </select>
              </div>
              <Button type="submit" disabled={isInviting}>
                <Mail className="h-3.5 w-3.5" />
                {isInviting ? 'Gönderiliyor...' : 'Davet oluştur'}
              </Button>
            </form>
          )}
        </Card>
      )}

      {resetLink && (
        <Card className="mb-6 p-5">
          <div className="mb-2 flex items-start justify-between gap-3">
            <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
              {resetLink.email} için sıfırlama linki oluşturuldu -- bu linki kendisine ilet
            </p>
            <button
              onClick={() => setResetLink(null)}
              className="cursor-pointer text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              aria-label="Kapat"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
            Henüz otomatik e-posta göndermiyoruz, link sadece bir kere gösteriliyor ve 1 gün
            geçerli.
          </p>
          <div className="flex items-center gap-2">
            <input
              readOnly
              value={resetLink.link}
              onFocus={(e) => e.target.select()}
              className="flex-1 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300"
            />
            <Button variant="secondary" onClick={() => void handleCopyResetLink()}>
              {resetLinkCopied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {resetLinkCopied ? 'Kopyalandı' : 'Kopyala'}
            </Button>
          </div>
        </Card>
      )}

      <Card>
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        ) : !members || members.length === 0 ? (
          <EmptyState
            icon={<Users className="h-6 w-6" />}
            title="Üye bulunamadı"
            description="Bu workspace'te henüz görüntülenecek bir ekip üyesi yok."
          />
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
                <th className="px-6 py-3 font-semibold">Üye</th>
                <th className="px-6 py-3 font-semibold">Roller</th>
                {canInvite && <th className="px-6 py-3 font-semibold" />}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {members.map((member) => (
                <tr key={member.id} className="transition-colors duration-150 hover:bg-slate-50/80 dark:hover:bg-slate-700/40">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div
                        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${avatarColor(member.email)}`}
                      >
                        {member.email.charAt(0).toUpperCase()}
                      </div>
                      <span className="font-medium text-slate-900 dark:text-slate-100">{member.email}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-wrap gap-1.5">
                      {member.roles.map((role) => (
                        <Badge key={role} tone="info">
                          {role}
                        </Badge>
                      ))}
                    </div>
                  </td>
                  {canInvite && (
                    <td className="px-6 py-4 text-right">
                      <Button
                        variant="secondary"
                        onClick={() => void handleResetPassword(member)}
                        disabled={resettingId === member.id}
                      >
                        <KeyRound className="h-3.5 w-3.5" />
                        {resettingId === member.id ? 'Oluşturuluyor...' : 'Şifreyi sıfırla'}
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {canInvite && invites && invites.length > 0 && (
        <>
          <p className="mb-3 mt-8 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            Bekleyen davetler ({invites.length})
          </p>
          <Card>
            <ul className="divide-y divide-slate-100 dark:divide-slate-700">
              {invites.map((invite) => (
                <li key={invite.id} className="flex items-center justify-between px-6 py-3.5">
                  <div>
                    <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{invite.email}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {invite.role} · {new Date(invite.expires_at).toLocaleDateString('tr-TR')} tarihine kadar geçerli
                    </p>
                  </div>
                  <Button
                    variant="danger"
                    onClick={() => void handleRevoke(invite.id)}
                    disabled={revokingId === invite.id}
                  >
                    <X className="h-3.5 w-3.5" />
                    {revokingId === invite.id ? 'İptal ediliyor...' : 'İptal et'}
                  </Button>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  )
}
