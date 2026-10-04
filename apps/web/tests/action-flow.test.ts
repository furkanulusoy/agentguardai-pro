import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { ActionFlow } from '../src/components/ActionFlow'
import type { ApprovalRequest } from '../src/types'

function render(status: string, execution_status: string | null, error: string | null = null) {
  return renderToStaticMarkup(createElement(ActionFlow, {
    approval: { status, execution_status, error } as ApprovalRequest,
  }))
}
describe('approval decision and provider outcome remain distinct', () => {
  it('does not claim completion for an approved but still executing operation', () => {
    const html = render('APPROVED', 'EXECUTING')
    expect(html).toContain('Yürütme bekleniyor')
    expect(html).not.toContain('Eylem tamamlandı')
  })
  it('explicitly displays an uncertain provider outcome', () => {
    const html = render('APPROVED', 'UNKNOWN', 'EXECUTION_OUTCOME_UNKNOWN')
    expect(html).toContain('Sonuç belirsiz')
    expect(html).not.toContain('Eylem tamamlandı')
  })
  it('shows completion only when execution succeeded', () => {
    expect(render('APPROVED', 'SUCCEEDED')).toContain('Eylem tamamlandı')
  })
  it('keeps a human rejection separate from successful execution', () => {
    expect(render('DENIED', 'DENIED')).not.toContain('Eylem tamamlandı')
  })
})
