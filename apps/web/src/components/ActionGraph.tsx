import { useMemo, useState } from 'react'
import { Mail, GitBranch, MessageSquare, Plug, User } from 'lucide-react'
import type { ApprovalRequest, ConnectorInfo } from '../types'
import { riskTone, statusTone } from './ui'

const CONNECTOR_ICON: Record<string, typeof Mail> = {
  gmail: Mail,
  github: GitBranch,
  slack: MessageSquare,
}

const TONE_HEX: Record<string, string> = {
  info: '#2563eb',
  warning: '#d97706',
  critical: '#dc2626',
  healthy: '#059669',
  slate: '#94a3b8',
}

const ROW_H = 42
const COL_USER = 90
const COL_CONNECTOR = 380
const COL_ACTION = 660
const MAX_ACTIONS = 12

/**
 * Real node graph: User (requested_by_email) -> Connector (credential_id)
 * -> Action (ApprovalRequest). No invented "Agent" entity, no fabricated
 * relationships -- see design-system/MASTER.md's data-honesty rule.
 */
export function ActionGraph({
  connectors,
  approvals,
  selectedId,
  onSelect,
}: {
  connectors: ConnectorInfo[]
  approvals: ApprovalRequest[]
  selectedId: string | null
  onSelect: (approval: ApprovalRequest) => void
}) {
  const [hoverConnectorId, setHoverConnectorId] = useState<string | null>(null)
  const [hoverUser, setHoverUser] = useState<string | null>(null)

  const activeConnectors = useMemo(() => connectors.filter((c) => !c.is_revoked), [connectors])
  const shownActions = useMemo(() => approvals.slice(0, MAX_ACTIONS), [approvals])

  const users = useMemo(() => {
    const seen = new Set<string>()
    const list: string[] = []
    for (const a of shownActions) {
      if (!seen.has(a.requested_by_email)) {
        seen.add(a.requested_by_email)
        list.push(a.requested_by_email)
      }
    }
    return list
  }, [shownActions])

  const userY = new Map(users.map((u, i) => [u, i * ROW_H + ROW_H / 2]))
  const connectorY = new Map(activeConnectors.map((c, i) => [c.id, i * ROW_H + ROW_H / 2]))
  const actionY = new Map(shownActions.map((a, i) => [a.id, i * ROW_H + ROW_H / 2]))

  const height = Math.max(users.length, activeConnectors.length, shownActions.length, 1) * ROW_H + 24

  const userConnectorEdges = useMemo(() => {
    const seen = new Set<string>()
    const edges: { user: string; connectorId: string }[] = []
    for (const a of shownActions) {
      const key = `${a.requested_by_email}__${a.credential_id}`
      if (!connectorY.has(a.credential_id) || seen.has(key)) continue
      seen.add(key)
      edges.push({ user: a.requested_by_email, connectorId: a.credential_id })
    }
    return edges
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownActions])

  if (shownActions.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-slate-400">
        Henüz görselleştirilecek bir ajan eylemi yok.
      </div>
    )
  }

  function isDimmed(userEmail?: string, connectorId?: string) {
    if (hoverConnectorId && connectorId !== undefined && connectorId !== hoverConnectorId) return true
    if (hoverUser && userEmail !== undefined && userEmail !== hoverUser) return true
    return false
  }

  return (
    <svg
      viewBox={`0 0 ${COL_ACTION + 24} ${height}`}
      className="w-full"
      style={{ height: `${height}px` }}
      role="img"
      aria-label="Kullanıcı, bağlantı ve eylem ilişki grafiği"
    >
      {/* edges: user -> connector */}
      {userConnectorEdges.map(({ user, connectorId }) => {
        const y1 = userY.get(user)
        const y2 = connectorY.get(connectorId)
        if (y1 === undefined || y2 === undefined) return null
        const dimmed = isDimmed(user, connectorId)
        return (
          <path
            key={`uc-${user}-${connectorId}`}
            d={`M ${COL_USER + 16} ${y1} C ${(COL_USER + COL_CONNECTOR) / 2} ${y1}, ${(COL_USER + COL_CONNECTOR) / 2} ${y2}, ${COL_CONNECTOR - 16} ${y2}`}
            fill="none"
            stroke="var(--graph-edge-stroke)"
            strokeWidth={dimmed ? 1 : 1.5}
            opacity={dimmed ? 0.25 : 0.8}
            className="transition-opacity duration-200"
          />
        )
      })}

      {/* edges: connector -> action */}
      {shownActions.map((a) => {
        const y1 = connectorY.get(a.credential_id)
        const y2 = actionY.get(a.id)
        if (y1 === undefined || y2 === undefined) return null
        const dimmed = isDimmed(a.requested_by_email, a.credential_id)
        const tone = TONE_HEX[statusTone(a.status)]
        return (
          <path
            key={`ca-${a.id}`}
            d={`M ${COL_CONNECTOR + 16} ${y1} C ${(COL_CONNECTOR + COL_ACTION) / 2} ${y1}, ${(COL_CONNECTOR + COL_ACTION) / 2} ${y2}, ${COL_ACTION - 10} ${y2}`}
            fill="none"
            stroke={a.status === 'PENDING' ? tone : 'var(--graph-edge-stroke)'}
            strokeWidth={a.status === 'PENDING' ? 1.5 : 1}
            opacity={dimmed ? 0.2 : 0.7}
            className="transition-opacity duration-200"
          />
        )
      })}

      {/* user nodes */}
      {users.map((email) => {
        const y = userY.get(email)!
        const dimmed = hoverUser !== null && hoverUser !== email
        return (
          <g
            key={email}
            transform={`translate(0, ${y})`}
            onMouseEnter={() => setHoverUser(email)}
            onMouseLeave={() => setHoverUser(null)}
            className="cursor-default transition-opacity duration-200"
            opacity={dimmed ? 0.35 : 1}
          >
            <circle r={13} fill="var(--graph-node-fill)" stroke="var(--graph-node-stroke)" strokeWidth={1} />
            <User x={-6} y={-6} width={12} height={12} color="var(--graph-label)" />
            <text x={20} y={4} fontSize={11} fontWeight={600} fill="var(--graph-label)">
              {email.length > 20 ? email.slice(0, 18) + '…' : email}
            </text>
            <title>{email}</title>
          </g>
        )
      })}

      {/* connector nodes */}
      {activeConnectors.map((c) => {
        const y = connectorY.get(c.id)
        if (y === undefined) return null
        const Icon = CONNECTOR_ICON[c.connector_type] ?? Plug
        const dimmed = hoverConnectorId !== null && hoverConnectorId !== c.id
        return (
          <g
            key={c.id}
            transform={`translate(${COL_CONNECTOR}, ${y})`}
            onMouseEnter={() => setHoverConnectorId(c.id)}
            onMouseLeave={() => setHoverConnectorId(null)}
            className="cursor-default transition-opacity duration-200"
            opacity={dimmed ? 0.35 : 1}
          >
            <circle r={15} fill="var(--graph-connector-fill)" stroke="var(--graph-connector-stroke)" strokeWidth={1.2} />
            <Icon x={-7} y={-7} width={14} height={14} color="var(--c-brand)" />
            <text x={22} y={4} fontSize={11} fontWeight={600} fill="var(--c-brand)">
              {c.label}
            </text>
            <title>{`${c.label} (${c.connector_type})`}</title>
          </g>
        )
      })}

      {/* action nodes */}
      {shownActions.map((a) => {
        const y = actionY.get(a.id)!
        const tone = TONE_HEX[a.status === 'PENDING' ? 'warning' : riskTone(a.risk_level)]
        const dimmed = isDimmed(a.requested_by_email, a.credential_id)
        const isSelected = a.id === selectedId
        return (
          <g
            key={a.id}
            transform={`translate(${COL_ACTION}, ${y})`}
            onClick={() => onSelect(a)}
            className="cursor-pointer transition-opacity duration-200"
            opacity={dimmed ? 0.25 : 1}
          >
            {isSelected && <circle r={11} fill="none" stroke={tone} strokeWidth={1.5} opacity={0.5} />}
            <circle
              r={6}
              fill={tone}
              className={a.status === 'PENDING' ? 'pulse-attention' : ''}
            />
            <text x={16} y={4} fontSize={11} fontWeight={500} fill="var(--graph-label)">
              {a.action}
            </text>
            <title>{`${a.connector_type}.${a.action} — ${a.risk_level} — ${a.status}`}</title>
          </g>
        )
      })}
    </svg>
  )
}
