import type { ReactNode } from 'react'

type IllustrationKind = 'chat' | 'documents' | 'retrieval' | 'report' | 'image' | 'database' | 'terminal' | 'map'

type DisplayEmptyProps = {
  title: string
  description?: string
  illustration?: IllustrationKind
  children?: ReactNode
  compact?: boolean
}

type MetricSummaryItem = {
  label: ReactNode
  value: ReactNode
  tone?: 'default' | 'success' | 'warning' | 'danger'
}

export function DisplayEmpty({ title, description, illustration = 'documents', children, compact = false }: DisplayEmptyProps) {
  return (
    <div className={compact ? 'display-empty display-empty-compact' : 'display-empty'}>
      <InlineIllustration kind={illustration} />
      <div className="display-empty-copy">
        <div className="display-empty-title">{title}</div>
        {description ? <div className="display-empty-text">{description}</div> : null}
      </div>
      {children ? <div className="display-empty-actions">{children}</div> : null}
    </div>
  )
}

export function MetricSummary({ items, className = '' }: { items: MetricSummaryItem[]; className?: string }) {
  return (
    <div className={className ? `display-summary ${className}` : 'display-summary'}>
      {items.map((item, index) => (
        <div key={index} className={item.tone ? `display-summary-item display-summary-${item.tone}` : 'display-summary-item'}>
          <span className="display-summary-label">{item.label}</span>
          <strong className="display-summary-value">{item.value}</strong>
        </div>
      ))}
    </div>
  )
}

export function FileTypeBadge({ label }: { label: string }) {
  return <span className="file-type-badge">{label}</span>
}

export function DisplayPlaceholder({ kind = 'image', text }: { kind?: IllustrationKind; text: string }) {
  return (
    <div className="display-placeholder">
      <InlineIllustration kind={kind} size={36} />
      <span>{text}</span>
    </div>
  )
}

export function InlineIllustration({ kind, size = 64 }: { kind: IllustrationKind; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 64 64',
    fill: 'none',
    xmlns: 'http://www.w3.org/2000/svg',
    'aria-hidden': true,
  }

  if (kind === 'chat') {
    return (
      <svg className="display-empty-figure" {...common}>
        <rect x="10" y="12" width="26" height="32" rx="4" stroke="currentColor" />
        <path d="M16 21h14M16 29h12M16 37h8" stroke="currentColor" strokeLinecap="round" />
        <path d="M34 29h16a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4h-7l-7 6v-6h-2a4 4 0 0 1-4-4V33a4 4 0 0 1 4-4Z" stroke="currentColor" />
        <path d="M39 38h9" stroke="currentColor" strokeLinecap="round" />
      </svg>
    )
  }

  if (kind === 'retrieval') {
    return (
      <svg className="display-empty-figure" {...common}>
        <circle cx="25" cy="25" r="12" stroke="currentColor" />
        <path d="m34 34 12 12" stroke="currentColor" strokeLinecap="round" />
        <rect x="33" y="12" width="16" height="6" rx="2" stroke="currentColor" />
        <rect x="39" y="23" width="14" height="6" rx="2" stroke="currentColor" />
        <rect x="43" y="34" width="12" height="6" rx="2" stroke="currentColor" />
      </svg>
    )
  }

  if (kind === 'report') {
    return (
      <svg className="display-empty-figure" {...common}>
        <rect x="13" y="10" width="38" height="44" rx="4" stroke="currentColor" />
        <path d="M21 22h16M21 31h22M21 40h10" stroke="currentColor" strokeLinecap="round" />
        <rect x="36" y="38" width="4" height="8" rx="1" fill="currentColor" />
        <rect x="42" y="32" width="4" height="14" rx="1" fill="currentColor" />
      </svg>
    )
  }

  if (kind === 'image') {
    return (
      <svg className="display-empty-figure" {...common}>
        <rect x="10" y="14" width="44" height="36" rx="4" stroke="currentColor" />
        <circle cx="24" cy="26" r="4" stroke="currentColor" />
        <path d="m16 44 12-12 8 8 5-5 8 9" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  }

  if (kind === 'database') {
    return (
      <svg className="display-empty-figure" {...common}>
        <ellipse cx="32" cy="16" rx="18" ry="7" stroke="currentColor" />
        <path d="M14 16v28c0 4 8 7 18 7s18-3 18-7V16" stroke="currentColor" />
        <path d="M14 30c0 4 8 7 18 7s18-3 18-7" stroke="currentColor" />
      </svg>
    )
  }

  if (kind === 'terminal') {
    return (
      <svg className="display-empty-figure" {...common}>
        <rect x="9" y="14" width="46" height="36" rx="4" stroke="currentColor" />
        <path d="m18 27 7 6-7 6M31 40h13" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  }

  if (kind === 'map') {
    return (
      <svg className="display-empty-figure" {...common}>
        <path d="m12 18 13-5 14 5 13-5v36l-13 5-14-5-13 5V18Z" stroke="currentColor" strokeLinejoin="round" />
        <path d="M25 13v36M39 18v36" stroke="currentColor" />
        <circle cx="32" cy="31" r="5" stroke="currentColor" />
      </svg>
    )
  }

  return (
    <svg className="display-empty-figure" {...common}>
      <rect x="13" y="10" width="34" height="44" rx="4" stroke="currentColor" />
      <path d="M22 23h20M22 32h16M22 41h12" stroke="currentColor" strokeLinecap="round" />
      <path d="M47 22h8M51 18v8" stroke="currentColor" strokeLinecap="round" />
    </svg>
  )
}
