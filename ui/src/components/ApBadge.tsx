interface Props {
  ap: 'H' | 'M' | 'L' | string
}

const labels: Record<string, string> = { H: 'High', M: 'Medium', L: 'Low' }

export default function ApBadge({ ap }: Props) {
  const cls =
    ap === 'H' ? 'badge-high' : ap === 'M' ? 'badge-medium' : 'badge-low'
  return <span className={cls}>{labels[ap] ?? ap}</span>
}
